"""Offline source-policy and transport regressions; never contacts a website."""

from urllib.parse import urlsplit

import pytest

from app.errors import DomainError
from app.fetch import fetch_public
from app.services.supplemental_research import candidate, matches


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
