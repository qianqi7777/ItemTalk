"""SSRF 安全下载器：逐跳解析并固定连接 IP，TLS 仍验证原始域名。"""

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.config import settings
from app.deadlines import bounded_call, remaining
from app.errors import DomainError


def encrypted_public_dns(host, port, **_kwargs):
    """为 Fake-IP 开发网络提供显式 DoH 选项，结果仍接受全部公网/映射地址检查。"""
    rows = []
    try:
        for kind in ("A", "AAAA"):
            response = httpx.get(
                "https://cloudflare-dns.com/dns-query",
                params={"name": host, "type": kind},
                headers={"Accept": "application/dns-json"},
                timeout=10,
                follow_redirects=False,
            )
            response.raise_for_status()
            value = response.json()
            if value.get("Status") != 0:
                raise ValueError("DNS resolution failed")
            for answer in value.get("Answer", []):
                if answer.get("type") in (1, 28):
                    address = ipaddress.ip_address(answer["data"])
                    family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
                    rows.append((family, socket.SOCK_STREAM, 6, "", (str(address), port)))
        return rows
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise OSError("Encrypted DNS resolution failed") from None


def public_addresses(url, resolver=None):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
    ):
        raise DomainError(422, "SOURCE_UNVERIFIED", "资料地址不符合安全要求")
    if resolver is None:
        # 来源登记和逐跳下载使用同一配置；DoH结果仍经过下方全部公网地址检查。
        resolver = (
            encrypted_public_dns
            if settings().fetch_dns_provider == "cloudflare"
            else socket.getaddrinfo
        )
    try:
        addresses = {
            row[4][0]
            for row in bounded_call(resolver, parts.hostname, 443, type=socket.SOCK_STREAM)
        }
        if not addresses or any(
            not ipaddress.ip_address(ip).is_global
            or ipaddress.ip_address(ip).ipv4_mapped is not None
            for ip in addresses
            if isinstance(ipaddress.ip_address(ip), ipaddress.IPv6Address)
        ):
            raise ValueError("unsafe address")
        if any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError("unsafe address")
        return parts, sorted(addresses)
    except (OSError, ValueError):
        raise DomainError(422, "SOURCE_UNVERIFIED", "资料地址无法安全访问") from None


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address):
        super().__init__(hostname, timeout=15, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            sock.settimeout(remaining(self.timeout))
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def fetch_public(url, maximum=50 * 1024 * 1024, *, accepted_mimes=None):
    for _ in range(4):
        parts, addresses = public_addresses(url)
        connection = PinnedHTTPS(parts.hostname, addresses[0])
        connection.timeout = remaining(15)
        try:
            path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parts.query:
                path += "?" + quote(parts.query, safe="=&%+/:?@!$'()*,-._~")
            connection.request(
                "GET", path, headers={"User-Agent": "ItemTalk/0.1", "Accept-Encoding": "identity"}
            )

            response_holder = {}

            def abort_connection(connection=connection, response_holder=response_holder):
                sock = getattr(connection, "sock", None)
                response = response_holder.get("response")
                if not sock and response is not None:
                    sock = getattr(
                        getattr(getattr(response, "fp", None), "raw", None), "_sock", None
                    )
                if sock:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                connection.close()

            response = bounded_call(connection.getresponse, on_timeout=abort_connection)
            response_holder["response"] = response
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader("Location") or "")
                continue
            if response.status != 200:
                raise DomainError(502, "UPSTREAM_SEARCH_ERROR", "资料下载失败")
            mime = (response.getheader("Content-Type") or "").split(";")[0].lower()
            if mime not in (accepted_mimes or {"application/pdf", "text/html", "text/plain"}):
                raise DomainError(422, "FILE_TYPE_NOT_ALLOWED", "资料格式不支持")
            chunks, size = [], 0
            while size <= maximum:
                timeout = remaining(15)
                sock = getattr(connection, "sock", None) or getattr(
                    getattr(getattr(response, "fp", None), "raw", None), "_sock", None
                )
                if sock:
                    sock.settimeout(timeout)
                chunk = bounded_call(
                    getattr(response, "read1", response.read),
                    min(65536, maximum + 1 - size),
                    on_timeout=abort_connection,
                )
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            data = b"".join(chunks)
            if len(data) > maximum:
                raise DomainError(413, "FILE_TOO_LARGE", "官方资料超过限制")
            return url, mime, data
        except (OSError, http.client.HTTPException):
            raise DomainError(502, "UPSTREAM_SEARCH_ERROR", "资料下载失败") from None
        finally:
            connection.close()
    raise DomainError(422, "SOURCE_UNVERIFIED", "资料重定向次数超限")
