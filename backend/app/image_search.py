"""Model-native reverse image search; remote captions never grant official authority."""

import ipaddress
from urllib.parse import urlsplit
from uuid import uuid4

from app.deadlines import remaining, time_limit
from app.errors import DomainError
from app.model_image_search import native_search
from app.recognition_vision import model_photo
from app.services.recognition_sources import safe_reference_url, thumbnail


def public_image_url(value):
    if not isinstance(value, str) or not safe_reference_url(value):
        return False
    try:
        address = ipaddress.ip_address(urlsplit(value).hostname)
    except ValueError:
        return True  # fetch_public resolves and pins DNS again before the actual download.
    return address.is_global and not getattr(address, "ipv4_mapped", None)


def search_images(gateway, images, subject, *, fetcher=None, on_source_rate_limited=None):
    from app.fetch import fetch_public

    fetcher = fetcher or fetch_public
    data, mime = images[-1]
    # Keep time for reference downloads and the final visual comparison.
    with time_limit(
        min(gateway.config.image_search_timeout_seconds, max(0.01, remaining(180) - 45))
    ):
        rows = native_search(gateway, model_photo(data, mime), subject)
    refs, visited = [], set()
    try:
        with time_limit(15):
            for row in rows[:5]:
                if not isinstance(row, dict):
                    continue
                url, title = row.get("url"), row.get("title", "相似产品图片")
                if not public_image_url(url) or url in visited or not isinstance(title, str):
                    continue
                visited.add(url)
                try:
                    final, mime, content = fetcher(
                        url,
                        maximum=2 * 1024 * 1024,
                        accepted_mimes={"image/jpeg", "image/png", "image/webp"},
                    )
                    if not public_image_url(final) or mime not in {
                        "image/jpeg",
                        "image/png",
                        "image/webp",
                    }:
                        continue
                    refs.append(
                        {
                            "id": uuid4().hex,
                            "url": final,
                            "title": title[:200],
                            "text": "图搜图结果标题（未核验，仅作外观候选线索）：" + title[:200],
                            "sourceKind": "third_party",
                            "imageData": thumbnail(content, mime),
                            "imageSearch": True,
                        }
                    )
                except DomainError as error:
                    if error.code == "SOURCE_RATE_LIMITED":
                        if on_source_rate_limited:
                            on_source_rate_limited()
                        if not refs:
                            raise
                        break
                    if error.status in {409, 429}:
                        raise
                    if error.status == 504:
                        break
                if len(refs) >= 3:
                    break
        if not refs:
            raise ValueError("No usable search images")
        return refs
    except (KeyError, TypeError, ValueError):
        raise DomainError(
            502, "UPSTREAM_SEARCH_ERROR", "图搜图没有返回可用的相似产品图片"
        ) from None
