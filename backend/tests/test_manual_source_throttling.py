"""A website cooldown applies to every redirect in one build, not model quota."""

import io
from urllib.parse import urlsplit

import pytest

from app.db import many, transaction
from app.fetch import fetch_public
from app.services import supplemental_index, supplemental_research
from tests.test_manual_alias_reuse import research_for
from tests.test_supplemental_index import TEXT, build_supplement

pytestmark = pytest.mark.postgres


def test_throttled_publisher_is_not_replayed_and_independent_publisher_can_build(
    client, monkeypatch
):
    import app.fetch as module

    calls = []

    class Response:
        def __init__(self, host):
            self.status = 429 if host == "manuals.plus" else 302 if host == "fccid.io" else 200
            self.body = io.BytesIO(("<article>" + TEXT + "</article>").encode())

        def getheader(self, key):
            return {
                "Retry-After": "120",
                "Location": "https://manuals.plus/roseselsa/ceramics-ultra-manual",
                "Content-Type": "text/html",
            }.get(key)

        def read(self, size):
            if self.status != 200:
                pytest.fail("Do not read throttled or redirected response bodies")
            return self.body.read(size)

    class Connection:
        sock = None

        def __init__(self, host, _address):
            self.host = host

        def request(self, *_a, **_kw):
            calls.append(self.host)

        def getresponse(self):
            return Response(self.host)

        def close(self):
            pass

    monkeypatch.setattr(module, "public_addresses", lambda u: (urlsplit(u), ["93.184.216.34"]))
    monkeypatch.setattr(module, "PinnedHTTPS", Connection)
    _, model, _ = build_supplement(client, monkeypatch, fetch=fetch_public)
    calls.clear()
    monkeypatch.setattr(
        supplemental_research,
        "discover",
        lambda *_: [
            {
                "url": "https://manuals.plus/roseselsa/ceramics-ultra-manual",
                "title": "ROSESELSA CERAMICS Ultra User Manual",
            },
            {
                "url": "https://manuals.plus/roseselsa/ceramics-ultra-manual-2",
                "title": "ROSESELSA CERAMICS Ultra User Manual",
            },
            {
                "url": "https://fccid.io/m/reference.pdf",
                "title": "ROSESELSA CERAMICS Ultra User Manual",
            },
            {
                "url": "https://device.report/manual/ceramics-ultra",
                "title": "ROSESELSA CERAMICS Ultra User Manual",
            },
        ],
    )
    job = research_for(model)
    rows = supplemental_index.retrieve(model["id"], "wireless pairing", [1.0] + [0.0] * 1023)
    assert rows and rows[0]["url"] == "https://device.report/manual/ceramics-ultra"
    assert calls == ["manuals.plus", "fccid.io", "device.report"]
    with transaction() as c:
        events = many(
            c,
            "SELECT payload FROM job_events WHERE job_id=:j AND event_type='error' "
            "AND payload->>'errorCode'='SOURCE_RATE_LIMITED'",
            j=job["id"],
        )
    assert len(events) == 1
    assert events[0]["payload"]["sourceHost"] == "manuals.plus"
    assert events[0]["payload"]["errorCode"] == "SOURCE_RATE_LIMITED"
    assert events[0]["payload"]["retryAfter"] == 120
    assert events[0]["payload"]["retryAt"]
