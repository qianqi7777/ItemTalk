"""上游错误只保留状态和受限错误码，原始正文、请求头和密钥不进入异常。"""

import re

from app.errors import DomainError


class UpstreamError(DomainError):
    def __init__(self, domain_code, status=None, provider_code=None):
        reasons = {
            400: "请求参数与上游协议不兼容",
            401: "上游密钥认证失败",
            403: "上游权限或可用额度不足",
            404: "所选模型或接口不可用",
            422: "请求参数与上游协议不兼容",
            429: "上游请求限流或额度限制",
        }
        super().__init__(502, domain_code, reasons.get(status, "上游服务暂时不可用"))
        self.upstream_status = status
        self.upstream_code = (
            provider_code
            if isinstance(provider_code, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", provider_code)
            and not provider_code.startswith("sk-")
            else None
        )


def response_error(response, domain_code="UPSTREAM_MODEL_ERROR"):
    """仅解析机器错误码，不返回可含用户数据的供应商 message。"""
    code = None
    try:
        payload = response.json()
        detail = payload.get("error", payload)
        if isinstance(detail, dict):
            code = detail.get("code")
    except (ValueError, AttributeError):
        pass
    return UpstreamError(domain_code, response.status_code, code)
