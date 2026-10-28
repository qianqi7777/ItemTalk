"""隔离解析不可信官方文档；限制文件大小、页数、输出和执行时间。"""

import base64
import io
import json
import os
import subprocess
import sys
from itertools import islice
from pathlib import Path

from app.deadlines import remaining
from app.errors import DomainError


def _image_resources(resources, depth=0):
    if depth > 2 or "/XObject" not in resources:
        return False
    for raw in islice(resources["/XObject"].values(), 100):
        obj = raw.get_object()
        if obj.get("/Subtype") == "/Image":
            return True
        if obj.get("/Subtype") == "/Form" and "/Resources" in obj:
            if _image_resources(obj["/Resources"], depth + 1):
                return True
    return False


def _parse(data, mime, include_scan_metadata=False):
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    try:
        if mime == "application/pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            # 部分公开官网PDF只设置所有者权限，空用户密码即可阅读；is_encrypted在
            # 解密后仍为True。只尝试空密码，真正需密码或不支持的加密仍拒绝。
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("Password-protected PDF")
            # EOS 550D官方中文手册260页；保留CPU/内存/总文本上限，仅扩展有界页数。
            if len(reader.pages) > 400:
                raise ValueError("Unsupported PDF")
            pages = [
                (index, (page.extract_text() or "")[:40000])
                for index, page in enumerate(reader.pages, 1)
            ]
            if include_scan_metadata:
                scanned = [
                    number
                    for number, text in pages
                    if (not text.strip() and reader.pages[number - 1].get_contents() is not None)
                    or (
                        sum(c.isalnum() for c in text) < 80
                        and "/Resources" in reader.pages[number - 1]
                        and _image_resources(reader.pages[number - 1]["/Resources"])
                    )
                ]
        elif mime == "text/plain":
            pages = [(1, data.decode("utf-8")[:200000])]
        else:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(data, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "iframe"]):
                tag.decompose()
            pages = [(1, soup.get_text("\n", strip=True)[:200000])]
        if sum(len(text) for _, text in pages) > 2_000_000:
            raise ValueError("Output limit")
        if mime == "application/pdf" and include_scan_metadata:
            return {"pages": pages, "scanPages": scanned}
        return pages
    except Exception:
        return None


def parse_document(data, mime, *, allow_empty_pdf=False, scan_pages=None):
    """固定子进程入口，环境白名单排除数据库、模型和 COS 凭据。"""
    if len(data) > 50 * 1024 * 1024:
        raise DomainError(413, "FILE_TOO_LARGE", "官方资料超过限制")
    environment = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP")
        if name in os.environ
    }
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    try:
        # argv 为固定应用入口，不包含外部文档、URL 或模型生成的命令。
        result = subprocess.run(
            [sys.executable, "-m", "app.parser", "--child"],  # noqa: S603
            input=json.dumps(
                {
                    "data": base64.b64encode(data).decode(),
                    "mime": mime,
                    "scanMetadata": scan_pages is not None,
                }
            ).encode(),
            capture_output=True,
            timeout=remaining(30),
            env=environment,
            check=False,
        )
        if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
            raise ValueError("Parser failed")
        pages = json.loads(result.stdout)
        if isinstance(pages, dict):
            if scan_pages is not None:
                scan_pages.extend(pages["scanPages"])
            pages = pages["pages"]
        if pages and mime == "application/pdf" and not any(text.strip() for _, text in pages):
            if allow_empty_pdf:
                return pages
            raise DomainError(
                422, "SOURCE_UNVERIFIED", "PDF没有可提取文字，可能是扫描版，请使用可读取文字的版本"
            )
        if not pages or not any(text.strip() for _, text in pages):
            raise DomainError(422, "SOURCE_UNVERIFIED", "资料解析失败或缺少可用文字")
        return pages
    except subprocess.TimeoutExpired:
        raise DomainError(422, "SOURCE_UNVERIFIED", "资料解析超时") from None
    except (ValueError, OSError):
        raise DomainError(422, "SOURCE_UNVERIFIED", "资料解析失败") from None


if __name__ == "__main__" and sys.argv[1:] == ["--child"]:
    payload = json.loads(sys.stdin.buffer.read(75 * 1024 * 1024))
    pages = _parse(
        base64.b64decode(payload["data"]), payload["mime"], payload.get("scanMetadata", False)
    )
    sys.stdout.buffer.write(json.dumps(pages, ensure_ascii=False).encode())
