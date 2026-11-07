"""Offline source-policy and transport regressions; never contacts a website."""

from urllib.parse import urlsplit

import pytest

from app.errors import DomainError
from app.fetch import fetch_public
from app.services.supplemental_research import candidate, matches


@pytest.mark.parametrize("brand", ["rose technics", "rosetechnics", "弱水时砂"])
def test_reviewed_identity_aliases_accept_the_same_full_model_manual(brand):
    model = dict(brand_name=brand, model_code="Ceramics Ultra", region=None, hardware_revision=None)
    assert candidate(
        dict(url="https://fccid.io/m/test.pdf", title="ROSESELSA CERAMICS Ultra User Manual"), model
    )
    assert matches(model, [(1, "ROSESELSA CERAMICS Ultra\nWireless pairing instructions")])
    assert not matches(model, [(1, "ROSESELSA CERAMICS Ultra Pro\nWireless pairing")])
    assert not matches(model, [(1, "OtherBrand CERAMICS Ultra\nWireless pairing")])


def test_shared_manufacturer_domains_do_not_make_subbrands_identity_aliases():
    model = dict(brand_name="HECATE", model_code="G5", region=None, hardware_revision=None)
    assert not matches(model, [(1, "EDIFIER G5\nInstructions")])


def test_manual_candidates_are_bounded_to_reviewed_publishers_and_exact_identity():
    model = dict(
        brand_name="ROSESELSA", model_code="CERAMICS Ultra", region=None, hardware_revision=None
    )
    assert candidate(
        dict(url="https://fccid.io/m/test.pdf", title="ROSESELSA CERAMICS Ultra User Manual"), model
    )
    assert not candidate(
        dict(url="https://other.example/manual.pdf", title="ROSESELSA CERAMICS Ultra User Manual"),
        model,
    )
    assert not candidate(
        dict(
            url="https://manuals.plus/category/roseselsa",
            title="ROSESELSA CERAMICS Ultra User Manual",
        ),
        model,
    )
    assert not matches(model, [(1, "Different brand Different model")], "ROSESELSA CERAMICS Ultra")


@pytest.mark.parametrize(
    "url",
    [
        "https://manuals.plus/search.html?q=CERAMICS-Ultra",
        "https://fccid.io/2BUP4-CERAMICSULTRA",
    ],
)
def test_a_manual_search_title_does_not_turn_navigation_into_a_document(url):
    model = dict(brand_name="ROSESELSA", model_code="CERAMICS Ultra")
    assert not candidate(dict(url=url, title="ROSESELSA CERAMICS Ultra User Manual"), model)


def test_source_429_does_not_consume_body_retry_or_user_quota(monkeypatch):
    import app.fetch as module

    calls = []

    class Response:
        status = 429

        def getheader(self, key):
            return "120" if key == "Retry-After" else None

        def read(self, *_):
            pytest.fail("A throttled response body must not be read")

    class Connection:
        sock = None

        def __init__(self, *_):
            pass

        def request(self, *_args, **_kwargs):
            calls.append(True)

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(module, "public_addresses", lambda url: (urlsplit(url), ["93.184.216.34"]))
    monkeypatch.setattr(module, "PinnedHTTPS", Connection)
    with pytest.raises(DomainError) as caught:
        fetch_public("https://manuals.plus/model-manual")
    assert caught.value.code == "SOURCE_RATE_LIMITED"
    assert caught.value.status != 429
    assert caught.value.retry_after == 120
    assert caught.value.source_host == "manuals.plus"
    assert calls == [True]


@pytest.mark.parametrize(
    ("original", "equivalent"),
    [("manuals.plus", "manuals.plus."), ("xn--bcher-kva.example", "bücher.example")],
)
def test_dns_equivalent_hosts_share_a_build_cooldown(monkeypatch, original, equivalent):
    import app.fetch as module

    calls = []

    class Response:
        status = 429

        def getheader(self, _):
            return "60"

    class Connection:
        sock = None

        def __init__(self, host, _):
            self.host = host

        def request(self, *_a, **_kw):
            calls.append(self.host)

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(module, "public_addresses", lambda u: (urlsplit(u), ["93.184.216.34"]))
    monkeypatch.setattr(module, "PinnedHTTPS", Connection)
    with module.source_cooldowns():
        for host in (original, equivalent):
            with pytest.raises(DomainError, match="限制访问"):
                fetch_public("https://" + host + "/manual")
    assert calls == [original]


def test_invalid_idna_still_uses_the_controlled_url_validation_error(monkeypatch):
    import app.fetch as module

    def invalid(_url):
        raise DomainError(422, "SOURCE_UNVERIFIED", "资料地址无法安全访问")

    monkeypatch.setattr(module, "public_addresses", invalid)
    with pytest.raises(DomainError) as caught:
        fetch_public("https://" + "a" * 64 + ".example/manual")
    assert caught.value.code == "SOURCE_UNVERIFIED"
