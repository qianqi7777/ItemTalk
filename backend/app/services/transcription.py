"""本人音频转写服务：先校验归属，返回待确认文本，不创建问答运行。"""

from app.errors import DomainError
from app.services.products import owned
from app.speech import ASRProvider
from app.storage import Storage


def transcribe_asset(connection, asset_id, user_id):
    asset = owned(connection, "assets", asset_id, user_id)
    if asset["status"] != "available":
        raise DomainError(409, "RESOURCE_CONFLICT", "请先确认录音上传")
    if not asset["mime_type"].startswith("audio/"):
        raise DomainError(422, "FILE_TYPE_NOT_ALLOWED", "请选择音频资产")
    data = Storage().read(asset["object_key"], maximum=7 * 1024 * 1024)
    provider = ASRProvider()
    try:
        return {
            "assetId": str(asset_id),
            "text": provider.transcribe(data, asset["mime_type"], user_id),
            "requiresConfirmation": True,
        }
    finally:
        provider.gateway.close()
