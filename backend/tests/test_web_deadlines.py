import http.client
import io
import time

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
