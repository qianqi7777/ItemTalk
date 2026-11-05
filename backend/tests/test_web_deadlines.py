import http.client
import io
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest

from app.deadlines import request_scope
from app.errors import DomainError
from app.fetch import fetch_public, public_addresses


def test_chunked_slow_framing_cannot_outlive_question_deadline(monkeypatch):
    from urllib.parse import urlsplit

    class SlowBody(io.RawIOBase):
        def __init__(self):
            self.data = iter(b"1;" + b"x" * 64 + b"\r\na\r\n0\r\n\r\n")

        def readable(self):
            return True

        def readinto(self, buffer):
            value = next(self.data, None)
            if value is None:
                return 0
            time.sleep(0.005)
            buffer[0] = value
            return 1

    class Socket:
        def makefile(self, *_args):
            headers = (
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n"
            )

            class Stream(io.BufferedReader):
                def __init__(self):
                    super().__init__(SlowBody())
                    self.headers = io.BytesIO(headers)

                def readline(self, limit=-1):
                    return (
                        self.headers.readline(limit)
                        if self.headers.tell() < len(headers)
                        else super().readline(limit)
                    )

            return Stream()

    class Connection:
        def __init__(self, *_args):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            response = http.client.HTTPResponse(Socket())
            response.begin()
            return response

        def close(self):
            pass

    monkeypatch.setattr("app.fetch.PinnedHTTPS", Connection)
    monkeypatch.setattr("app.fetch.public_addresses", lambda url: (urlsplit(url), ["8.8.8.8"]))
    start = time.monotonic()
    with request_scope(start + 0.04), pytest.raises(DomainError):
        fetch_public("https://safe.example/", maximum=100)
    assert time.monotonic() - start < 0.2


def test_dns_lookup_cannot_outlive_question_deadline():
    def slow(*_args, **_kwargs):
        time.sleep(0.3)
        return [(2, 1, 6, "", ("8.8.8.8", 443))]

    start = time.monotonic()
    with request_scope(start + 0.04), pytest.raises(DomainError):
        public_addresses("https://safe.example/", resolver=slow)
    assert time.monotonic() - start < 0.2


def test_slow_body_is_checked_between_reads(monkeypatch):
    class Response:
        status = 200

        def getheader(self, _name):
            return "text/plain"

        def read(self, _maximum):
            return b"old unchecked read"

        def read1(self, _maximum):
            time.sleep(0.02)
            return b"x"

    class Connection:
        def __init__(self, *_args):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("app.fetch.PinnedHTTPS", Connection)
    monkeypatch.setattr(
        "app.fetch.public_addresses",
        lambda url: (__import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(url), ["8.8.8.8"]),
    )
    with request_scope(time.monotonic() + 0.04), pytest.raises(DomainError):
        fetch_public("https://safe.example/", maximum=100)


def test_unicode_manual_path_is_encoded_before_http_request(monkeypatch):
    from urllib.parse import urlsplit

    class Response:
        status = 200

        def __init__(self):
            self.chunks = iter([b"manual", b""])

        def getheader(self, _name):
            return "text/plain"

        def read(self, _maximum):
            return next(self.chunks)

    class Connection:
        def __init__(self, *_args):
            pass

        def request(self, _method, path, **_kwargs):
            assert path.isascii() and "%E8%AF%B4" in path

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("app.fetch.PinnedHTTPS", Connection)
    monkeypatch.setattr("app.fetch.public_addresses", lambda url: (urlsplit(url), ["8.8.8.8"]))
    assert fetch_public("https://safe.example/G3说明书.pdf")[2] == b"manual"


@pytest.mark.parametrize(
    "retry_after,expected",
    [
        ("120", 120),
        ("Thu, 05 Nov 2026 00:02:00 GMT", 120),
        (None, 60),
        ("not-a-date", 60),
        ("-20", 60),
        ("Thu, 05 Nov 2026 00:00:00 GMT", 1),
        ("99999999999999999999", 86400),
    ],
)
def test_redirected_source_throttle_uses_final_host_and_bounded_retry_after(
    monkeypatch, retry_after, expected
):
    import app.fetch as module

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 5, tzinfo=UTC)

    hosts, resolved, closed = [], [], []

    class Response:
        def __init__(self, status):
            self.status = status

        def getheader(self, key):
            return {
                "Location": "https://limited.example/manual.pdf",
                "Retry-After": retry_after,
            }.get(key)

        def read(self, *_args):
            pytest.fail("429 and redirect bodies must not be read")

    class Connection:
        def __init__(self, host, _address):
            self.host = host
            hosts.append(host)

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return Response(302 if self.host == "safe.example" else 429)

        def close(self):
            closed.append(self.host)

    def addresses(url):
        resolved.append(urlsplit(url).hostname)
        return urlsplit(url), ["93.184.216.34"]

    monkeypatch.setattr(module, "datetime", FixedDateTime)
    monkeypatch.setattr(module, "PinnedHTTPS", Connection)
    monkeypatch.setattr(module, "public_addresses", addresses)

    with pytest.raises(DomainError) as caught:
        fetch_public("https://safe.example/manual")

    assert caught.value.code == "SOURCE_RATE_LIMITED"
    assert caught.value.status == 503
    assert caught.value.source_host == "limited.example"
    assert caught.value.retry_after == expected
    assert hosts == resolved == closed == ["safe.example", "limited.example"]
