"""领域异常与脱敏响应，禁止将数据库或供应商异常正文发给客户端。"""

from uuid import uuid4


class DomainError(Exception):
    def __init__(self, status, code, message):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)

    def body(self):
        return {"code": self.code, "message": self.message, "requestId": str(uuid4())}


def missing(code="PRODUCT_NOT_FOUND"):
    raise DomainError(404, code, "资源不存在")
