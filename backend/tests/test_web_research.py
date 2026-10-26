"""Web evidence is fetched, bounded, attributable and separate from official chunks."""

import time

import pytest

from app.errors import DomainError
from app.services import web_research as web

MODEL = {
    "brand_name": "Acme",
    "model_code": "Pro100",
    "official_domains": [],
    "region": None,
    "hardware_revision": None,
}
QUESTION = "蓝牙连接不上怎么办？我的邮箱 me@example.com 密码 abc-secret"


@pytest.fixture
def sources(monkeypatch):
    calls, fetched = [], []
    rows = [{"title": "Acme Pro100 蓝牙教程", "url": "https://guide.example/pro100"}]
    pages = {
        rows[0][
            "url"
        ]: "Acme Pro100 使用说明。蓝牙连接：打开电脑蓝牙设置，选择 Acme Pro100，确认连接。"
    }

    class Search:
        def search(self, query, **kwargs):
            calls.append(query)
            return list(rows)

    def fetch(url, **kwargs):
        fetched.append(url)
        value = pages[url]
        if isinstance(value, Exception):
            raise value
        return url, "text/html", value.encode()

    monkeypatch.setattr(web, "SearchGateway", Search)
    monkeypatch.setattr(web, "fetch_public", fetch)
    monkeypatch.setattr(web, "parse_document", lambda data, mime, **kwargs: [(1, data.decode())])
    return calls, fetched, rows, pages


def research(**kwargs):
    return web.research(
        MODEL,
        QUESTION,
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
        **kwargs,
    )


def test_unknown_brand_still_searches_and_reads_third_party(sources):
    calls, fetched, _, _ = sources
    result = research()
    assert calls and fetched and result["status"] == "found"
    assert all("me@example.com" not in q and "abc-secret" not in q for q in calls)
    source = result["sources"][0]
    assert source["sourceKind"] == "third_party" and "蓝牙连接" in source["excerpt"]
    assert source["url"] == fetched[0] and source["pageStart"] is None


def test_trusted_official_page_is_prioritized(sources):
    _, fetched, rows, pages = sources
    url = "https://acme.example/pro100"
    rows.append({"title": "Acme Pro100", "url": url})
    pages[url] = pages[rows[0]["url"]]
    result = web.research(
        {**MODEL, "official_domains": ["acme.example"]},
        QUESTION,
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
    )
    assert fetched[0] == url and result["sources"][0]["sourceKind"] == "official"


@pytest.mark.parametrize(
    "body", ["Acme Pro1000 蓝牙设置", "Acme Pro100 升级版蓝牙连接方法", "Other Pro100 蓝牙设置"]
)
def test_wrong_or_near_model_is_not_evidence(sources, body):
    _, _, rows, pages = sources
    rows[0]["title"] = body
    pages[rows[0]["url"]] = body
    assert research()["sources"] == []


def test_known_brand_conflict_is_reported_without_using_steps(sources):
    _, _, rows, pages = sources
    rows[0]["title"] = "HECATE G3M PRO 说明书"
    pages[rows[0]["url"]] = "HECATE G3M PRO 蓝牙连接使用说明。"
    result = web.research(
        {**MODEL, "brand_name": "迈从", "model_code": "G3MPro"},
        QUESTION,
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
    )
    assert result["status"] == "identity_conflict" and not result["sources"]
    assert result["conflict"]["url"] == rows[0]["url"]


def test_unreadable_result_does_not_turn_search_title_into_evidence(sources):
    _, _, rows, pages = sources
    pages[rows[0]["url"]] = DomainError(502, "UPSTREAM_SEARCH_ERROR", "不可读")
    result = research()
    assert not result["sources"] and result["reason"] == "unreadable"


def test_matching_search_title_cannot_override_wrong_product_in_body(sources):
    _, _, rows, pages = sources
    pages[rows[0]["url"]] = "Other Pro1000 蓝牙连接教程，选择其他设备。"
    assert not research()["sources"]


def test_sensitive_steps_reject_third_party_even_when_text_matches(sources):
    result = web.research(
        MODEL, "如何刷固件？", user="test", cancelled=lambda: False, deadline=time.monotonic() + 40
    )
    assert not result["sources"]


def test_query_and_fetch_limits(sources):
    calls, fetched, rows, pages = sources
    rows[:] = [{"title": "Other", "url": f"https://guide.example/{i}"} for i in range(20)]
    pages.update({r["url"]: "无型号介绍" for r in rows})
    research()
    assert len(calls) <= 3 and len(fetched) <= 5


def test_cancellation_stops_before_search(sources):
    with pytest.raises(DomainError) as error:
        web.research(
            MODEL, QUESTION, user="test", cancelled=lambda: True, deadline=time.monotonic() + 40
        )
    assert error.value.status == 409 and sources[0] == []


def test_expired_deadline_does_not_search(sources):
    result = web.research(
        MODEL, QUESTION, user="test", cancelled=lambda: False, deadline=time.monotonic() - 1
    )
    assert result["reason"] == "timeout" and sources[0] == []


def test_recent_cache_is_reused_but_force_searches(sources):
    first = research()
    count = len(sources[0])
    second = research(cached=first)
    assert second["cached"] and len(sources[0]) == count
    research(cached=first, force=True)
    assert len(sources[0]) > count


def test_changed_official_trust_invalidates_cache(sources):
    first = research()
    count = len(sources[0])
    web.research(
        {**MODEL, "official_domains": ["new.example"]},
        QUESTION,
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
        cached=first,
    )
    assert len(sources[0]) > count


def test_explicit_official_domain_removal_overrides_reviewed_registry():
    aliases, domains = web.identity({**MODEL, "brand_name": "迈从", "official_domains": []})
    assert "mchose" in aliases and domains == []


def test_direct_official_pdf_link_establishes_cdn_provenance(sources):
    _, _, rows, pages = sources
    rows[:] = [{"title": "Acme Pro100 官方支持", "url": "https://acme.example/pro100"}]
    pages[rows[0]["url"]] = '<a href="https://cdn.example/manual.pdf">说明书</a>'
    pages["https://cdn.example/manual.pdf"] = "Acme Pro100 蓝牙连接使用说明。"
    result = web.research(
        {**MODEL, "official_domains": ["acme.example"]},
        QUESTION,
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
    )
    assert result["sources"][0]["sourceKind"] == "official"
    assert result["sources"][0]["linkedFrom"] == rows[0]["url"]


def test_arbitrary_redirect_cannot_become_official(sources, monkeypatch):
    _, _, rows, _ = sources
    rows[:] = [{"title": "Acme Pro100 官方支持", "url": "https://acme.example/pro100"}]
    monkeypatch.setattr(
        web,
        "fetch_public",
        lambda *_args, **_kwargs: (
            "https://unknown.example/doc",
            "text/html",
            "Acme Pro100 蓝牙说明".encode(),
        ),
    )
    result = web.research(
        {**MODEL, "official_domains": ["acme.example"]},
        QUESTION,
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
    )
    assert result["sources"][0]["sourceKind"] != "official"


def test_revoked_source_invalidates_cache_and_cannot_be_used(sources):
    first = research()
    result = web.research(
        {**MODEL, "blocked_urls": [first["sources"][0]["url"]]},
        QUESTION,
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
        cached=first,
    )
    assert not result["cached"] and not result["sources"]


def test_cache_cannot_promote_third_party_to_sensitive_steps(sources):
    first = research()
    result = web.research(
        MODEL,
        "蓝牙如何恢复出厂设置？",
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
        cached=first,
    )
    assert not result["sources"]


@pytest.mark.parametrize(
    "title,body",
    [
        ("Acme Pro100 A", "Acme Pro100 A 蓝牙连接教程"),
        ("Acme Pro200", "Acme Pro100 蓝牙连接：其他推荐，Pro200的步骤如下"),
    ],
)
def test_variant_or_different_primary_title_is_not_exact(title, body):
    assert web._match(MODEL, title, body, False) != "matched"


def test_explicit_multi_model_manual_can_cover_base_and_variant():
    assert (
        web._match(
            {**MODEL, "brand_name": "迈从", "model_code": "G3"},
            "G3 / G3 A 说明书",
            "MCHOSE G3 / G3 A 无线鼠标使用说明",
            True,
        )
        == "matched"
    )


@pytest.mark.parametrize("suffix", [" A使用说明", " Pro使用说明", "-A 使用说明"])
def test_chinese_adjacent_or_hyphenated_variant_is_not_base(suffix):
    text = "MCHOSE G3" + suffix
    assert (
        web._match({**MODEL, "brand_name": "迈从", "model_code": "G3"}, text, text, True)
        == "wrong_model"
    )


def test_generic_first_results_do_not_starve_question_specific_search(sources, monkeypatch):
    calls, _, rows, pages = sources
    wrong = [
        {"title": "Acme Pro100 规格", "url": f"https://guide.example/spec-{i}"} for i in range(10)
    ]
    pages.update({r["url"]: "Acme Pro100 规格介绍" for r in wrong})

    def search(_self, query, **_kwargs):
        calls.append(query)
        return list(rows) if "教程" in query else wrong

    monkeypatch.setattr(web.SearchGateway, "search", search)
    assert research()["sources"] and len(calls) == 3


def test_site_query_does_not_exclude_manual_without_question_keywords():
    queries, _, _ = web.queries({**MODEL, "official_domains": ["acme.example"]}, QUESTION)
    assert queries[1] == "site:acme.example Pro100"
    assert queries[0] == "Acme Pro100 说明书"


def test_matching_manual_is_read_before_unrelated_commerce_results(sources):
    _, fetched, rows, pages = sources
    good = rows[0]
    noise = [
        {"title": "Other Pro200 包邮优惠", "url": f"https://shop.example/{i}"} for i in range(8)
    ]
    pages.update({r["url"]: "其他产品" for r in noise})
    rows[:] = noise + [good]
    assert research()["sources"] and fetched[0] == good["url"]


def test_manufacturer_community_post_is_third_party(sources):
    _, _, rows, pages = sources
    url = "https://community.acme.example/pro100"
    rows[:] = [{"title": "Acme Pro100 蓝牙教程", "url": url}]
    pages[url] = "Acme Pro100 蓝牙连接教程"
    result = web.research(
        {**MODEL, "official_domains": ["acme.example"]},
        QUESTION,
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
    )
    assert result["sources"][0]["sourceKind"] == "third_party"


def test_site_support_page_and_linked_pdf_share_reserved_budget(sources, monkeypatch):
    calls, _, _, pages = sources
    url = "https://acme.example/product/support-100.html"
    pages[url] = '<a href="https://acme.example/Pro100.pdf">Acme Pro100 说明书</a>'
    pages["https://acme.example/Pro100.pdf"] = "Acme Pro100 蓝牙连接方法"
    pages["https://acme.example/specs"] = "Acme Pro100 规格"

    def search(_self, query, **_kwargs):
        calls.append(query)
        return (
            [
                {"title": "Acme Pro100 规格", "url": "https://acme.example/specs"},
                {"title": "Acme", "url": url},
            ]
            if query.startswith("site:")
            else []
        )

    monkeypatch.setattr(web.SearchGateway, "search", search)
    result = web.research(
        {**MODEL, "official_domains": ["acme.example"]},
        QUESTION,
        user="test",
        cancelled=lambda: False,
        deadline=time.monotonic() + 40,
    )
    assert result["sources"] and result["sources"][0]["url"].endswith("Pro100.pdf")
