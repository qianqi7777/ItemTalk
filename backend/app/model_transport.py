"""Public HTTPS transport for administrator-entered model endpoints, including SSE."""

import http.client

import httpx

from app.deadlines import bounded_call, remaining
from app.errors import DomainError
from app.fetch import PinnedHTTPS, public_addresses


class ModelResponseStream(httpx.SyncByteStream):
    def __init__(self, connection, response, timeout):
        self.connection = connection
        self.response = response
        self.timeout = timeout

    def __iter__(self):
        size = 0
        try:
            while True:
                sock = getattr(self.connection, "sock", None) or getattr(
                    getattr(getattr(self.response, "fp", None), "raw", None), "_sock", None
                )
                if sock:
                    sock.settimeout(remaining(self.timeout))
                chunk = bounded_call(self.response.read1, 65536, on_timeout=self.close)
                if not chunk:
                    return
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    raise DomainError(502, "UPSTREAM_MODEL_ERROR", "模型返回内容超过限制")
                yield chunk
        except (OSError, http.client.HTTPException):
            raise httpx.ReadError("Model response failed") from None
        finally:
            self.close()

    def close(self):
        self.response.close()
        self.connection.close()


class PublicModelTransport(httpx.BaseTransport):
    def handle_request(self, request):
        # Resolve for every connection, pin the selected IP, retain the hostname for TLS.
        parts, addresses = public_addresses(str(request.url))
        if parts.query or parts.fragment:
            raise DomainError(422, "VALIDATION_ERROR", "模型地址不能包含查询参数或片段")
        connection = PinnedHTTPS(parts.hostname, addresses[0])
        timeout = request.extensions.get("timeout", {}).get("read") or 45
        connection.timeout = remaining(min(timeout, 45))
        try:
            connection.request(
                request.method,
                parts.path or "/",
                body=request.read(),
                headers={**dict(request.headers), "Accept-Encoding": "identity"},
            )
            response = bounded_call(connection.getresponse, on_timeout=connection.close)
            if 300 <= response.status < 400:
                raise DomainError(
                    502, "UPSTREAM_MODEL_ERROR", "模型地址发生重定向，请填写最终API地址"
                )
            return httpx.Response(
                response.status,
                headers=response.getheaders(),
                stream=ModelResponseStream(connection, response, timeout),
                request=request,
            )
        except (OSError, http.client.HTTPException):
            connection.close()
            raise httpx.ConnectError("Model connection failed", request=request) from None
        except BaseException:
            connection.close()
            raise
