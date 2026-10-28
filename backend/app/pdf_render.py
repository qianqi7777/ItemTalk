"""Render one untrusted PDF page in a credential-free, resource-bounded child."""

import base64
import io
import json
import math
import os
import subprocess
import sys
from pathlib import Path

from app.deadlines import remaining
from app.errors import DomainError


def _render(data, page_no):
    if sys.platform != "win32":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(data, password="") as pdf:
        if not 1 <= page_no <= len(pdf) <= 400:
            raise ValueError("Page limit")
        page = pdf[page_no - 1]
        try:
            width, height = page.get_size()
            if (
                not all(math.isfinite(v) and 1 <= v <= 14400 for v in (width, height))
                or max(width, height) / min(width, height) > 20
            ):
                raise ValueError("Page dimensions")
            scale = min(3, 2400 / max(width, height))
            bitmap = page.render(scale=scale, may_draw_forms=False)
            try:
                image = bitmap.to_pil().convert("RGB")
                try:
                    output = io.BytesIO()
                    image.save(output, "JPEG", quality=88)
                    return output.getvalue()
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()


def render_page(data, page_no):
    if len(data) > 50 * 1024 * 1024 or type(page_no) is not int or not 1 <= page_no <= 400:
        raise DomainError(422, "SOURCE_UNVERIFIED", "扫描页文件大小或页码超过限制")
    environment = {
        name: os.environ[name]
        for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP")
        if name in os.environ
    }
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.pdf_render", "--child"],  # noqa: S603
            input=json.dumps({"data": base64.b64encode(data).decode(), "page": page_no}).encode(),
            capture_output=True,
            timeout=remaining(30),
            env=environment,
            check=False,
        )
        if result.returncode or not result.stdout or len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError("Render failed")
        return result.stdout
    except (subprocess.TimeoutExpired, OSError, ValueError):
        raise DomainError(422, "SOURCE_UNVERIFIED", "扫描页渲染失败、超时或尺寸超过限制") from None


if __name__ == "__main__" and sys.argv[1:] == ["--child"]:
    payload = json.loads(sys.stdin.buffer.read(75 * 1024 * 1024))
    sys.stdout.buffer.write(_render(base64.b64decode(payload["data"]), payload["page"]))
