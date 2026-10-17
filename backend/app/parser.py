"""隔离解析不可信官方文档；限制文件大小、页数、输出和执行时间。"""

import base64
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from app.errors import DomainError


def _parse(data, mime):
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
        return pages
    except Exception:
        return None


def parse_document(data, mime):
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
            input=json.dumps({"data": base64.b64encode(data).decode(), "mime": mime}).encode(),
            capture_output=True,
            timeout=30,
            env=environment,
            check=False,
        )
        if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
            raise ValueError("Parser failed")
        pages = json.loads(result.stdout)
        if not pages or not any(text.strip() for _, text in pages):
            raise DomainError(422, "SOURCE_UNVERIFIED", "资料解析失败或缺少可用文字")
        return pages
    except subprocess.TimeoutExpired:
        raise DomainError(422, "SOURCE_UNVERIFIED", "资料解析超时") from None
    except (ValueError, OSError):
        raise DomainError(422, "SOURCE_UNVERIFIED", "资料解析失败") from None


if __name__ == "__main__" and sys.argv[1:] == ["--child"]:
    payload = json.loads(sys.stdin.buffer.read(75 * 1024 * 1024))
    pages = _parse(base64.b64decode(payload["data"]), payload["mime"])
    sys.stdout.buffer.write(json.dumps(pages, ensure_ascii=False).encode())
