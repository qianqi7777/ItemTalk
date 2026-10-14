"""本地/COS 对象适配，限制路径、大小和图片像素并去除 EXIF。"""

import hashlib
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from qcloud_cos.cos_exception import CosException, CosServiceError

from app.config import settings
from app.errors import DomainError
from app.security import token_for

Image.MAX_IMAGE_PIXELS = 20_000_000


class Storage:
    """业务存储适配器；COS 原始异常不得越过服务层响应或任务记录边界。"""

    def __init__(self):
        self.config = settings()
        self.client = None
        if self.config.storage_backend == "cos":
            from qcloud_cos import CosConfig, CosS3Client

            self.client = CosS3Client(
                CosConfig(
                    Region=self.config.cos_region,
                    SecretId=self.config.cos_secret_id.get_secret_value(),
                    SecretKey=self.config.cos_secret_key.get_secret_value(),
                    Scheme="https",
                )
            )

    def path(self, key):
        root = Path(self.config.storage_directory).resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Invalid object key")
        return path

    def url(self, asset, write=False):
        now = datetime.now(UTC)
        ttl = 900
        if write and asset["expires_at"]:
            # 幂等重放不能延长旧 PUT 签名，清理时间以最初上传期限为准。
            remaining = int((asset["expires_at"] - now).total_seconds())
            if remaining <= 0:
                raise DomainError(409, "RESOURCE_CONFLICT", "上传已过期，请创建新上传")
            ttl = min(ttl, remaining)
        expires = now + timedelta(seconds=ttl)
        if self.client:
            url = self.client.get_presigned_url(
                Bucket=self.config.cos_bucket,
                Key=asset["object_key"],
                Method="PUT" if write else "GET",
                Expired=ttl,
            )
        else:
            action = "upload" if write else "read"
            token = token_for(asset["id"], action, owner=str(asset["owner_user_id"]), exp=expires)
            base = self.config.app_public_url.rstrip("/")
            url = f"{base}/api/v1/assets/{asset['id']}/content?token={token}"
        return url, expires.isoformat()

    def read(self, key, maximum=50 * 1024 * 1024):
        if self.client:
            response = self._cos_call("get_object", Bucket=self.config.cos_bucket, Key=key)
            stream = response["Body"].get_raw_stream()
            try:
                data = stream.read(maximum + 1)
            finally:
                stream.close()
        else:
            with self.path(key).open("rb") as file:
                data = file.read(maximum + 1)
        if len(data) > maximum:
            raise DomainError(413, "FILE_TOO_LARGE", "文件超过限制")
        return data

    def put(self, key, data, mime):
        if self.client:
            self._cos_call(
                "put_object", Bucket=self.config.cos_bucket, Key=key, Body=data, ContentType=mime
            )
        else:
            path = self.path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Confirmed objects always use a fresh random key.
            with path.open("xb") as file:
                file.write(data)

    def delete(self, key):
        if self.client:
            self._cos_call("delete_object", Bucket=self.config.cos_bucket, Key=key)
        else:
            self.path(key).unlink(missing_ok=True)

    def _cos_call(self, operation, **kwargs):
        """不存在的上传对象返回受控缺失；其他供应商失败统一脱敏。"""
        if operation not in {"get_object", "put_object", "delete_object"}:
            raise ValueError("Unsupported storage operation")
        try:
            return getattr(self.client, operation)(**kwargs)
        except CosServiceError as error:
            if (
                operation == "get_object"
                and error.get_status_code() == 404
                and error.get_error_code() == "NoSuchKey"
            ):
                raise FileNotFoundError("Upload object is not available") from None
            raise DomainError(503, "INTERNAL_ERROR", "对象存储暂时不可用，请稍后重试") from None
        except CosException:
            raise DomainError(503, "INTERNAL_ERROR", "对象存储暂时不可用，请稍后重试") from None


def sanitize_image(data, mime):
    try:
        with Image.open(io.BytesIO(data)) as image:
            expected = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}.get(mime)
            if image.format != expected or image.width * image.height > 20_000_000:
                raise ValueError("format or pixel limit")
            image.load()
            oriented = ImageOps.exif_transpose(image)
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
            else:
                clean = oriented.convert("RGB")
            output = io.BytesIO()
            clean.save(output, format="JPEG", quality=90)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise DomainError(422, "FILE_TYPE_NOT_ALLOWED", "图片格式或尺寸无效") from None


def immutable_key(scope):
    return f"{scope}/{uuid4().hex}"


def sha256(data):
    return hashlib.sha256(data).hexdigest()
