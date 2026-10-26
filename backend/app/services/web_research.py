"""Question-time public research. Reading a page never grants official trust."""

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urldefrag, urljoin, urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup

from app.adapters import SearchGateway
from app.deadlines import request_scope
from app.errors import DomainError
from app.fetch import fetch_public
from app.parser import parse_document
from app.safety import risk_of
from app.security import normalize
from app.services.general_advice import requires_evidence
from app.services.source_discovery import allowed_url

# Reviewed public manufacturer identities, shared by every model of these brands.
# This metadata is only provenance for this research path, never a database trust grant.
BRANDS = (
    (("迈从", "mchose"), ("maicong.cn", "mchose.com.cn", "mchose.store")),
    (("漫步者", "edifier", "hecate"), ("edifier.com", "edifier.com.cn", "hecategaming.com")),
    (("索尼", "sony"), ("sony.com", "sony.com.cn", "sony.net")),
    (("罗技", "logitech", "logi"), ("logitech.com", "logitech.com.cn", "logi.com")),
)
TOPICS = (
    (r"蓝牙|bluetooth|配对|pair", "蓝牙配对", r"蓝牙|bluetooth|配对|pair"),
    (r"dpi|灵敏度|指针|pointer|sensitivity", "DPI 指针灵敏度设置", r"dpi|灵敏度|指针|sensitivity"),
    (r"wifi|wi-fi|网络|联网", "WiFi 网络连接设置", r"wifi|wi-fi|网络"),
    (r"固件|firmware|刷机", "固件 firmware 官方支持", r"固件|firmware"),
    (r"重置|复位|恢复出厂|reset", "恢复出厂设置 reset", r"重置|复位|恢复出厂|reset"),
    (r"充电|电压|电流|电源", "供电 充电 规格", r"充电|电压|电流|电源|charging"),
    (r"清洁|清理|clean", "清洁 维护 方法", r"清洁|维护|clean"),
    (r"驱动|driver|usb|接收器|连接|识别", "USB 接收器 连接 使用方法", r"usb|接收器|连接|driver"),
)


def official_url(url, domains):
    """Manufacturer-hosted community contributions do not carry manufacturer authority."""
    if not allowed_url(url, domains):
        return False
    host = urlsplit(url).hostname or ""
    return not any(
        re.match(r"^(?:bbs|forums?|community|discussions?|answers)(?:-|$)", label)
        for label in host.split(".")[:-2]
    )


def identity(model):
    brand = normalize(model["brand_name"])
    for aliases, domains in BRANDS:
        if brand in aliases:
            registered = model.get("official_domains")
            return aliases, sorted(domains if registered is None else registered)
    return (brand,), sorted(model.get("official_domains") or [])


def contains(text, value):
    characters = [c for c in normalize(value) if c not in " -_"]
    if not characters:
        return None
    pattern = r"[\s_-]*".join(re.escape(c) for c in characters)
    # Chinese brand names commonly touch Latin model codes without a word separator.
    prefix = r"(?<![a-z0-9])" if re.fullmatch(r"[a-z0-9]", characters[0]) else ""
    suffix = r"(?![a-z0-9])" if re.fullmatch(r"[a-z0-9]", characters[-1]) else ""
    return re.search(prefix + pattern + suffix, normalize(text))


def queries(model, question):
    """Only approved public identity and a fixed intent dictionary leave the service."""
    aliases, domains = identity(model)
    topic, keywords = "使用方法 故障排查", r"使用|设置|setup|connect"
    for pattern, name, words in TOPICS:
        if re.search(pattern, normalize(question)):
            topic, keywords = name, words
            break
    operating = next(
        (
            name
            for name in ("windows 11", "windows 10", "macos", "android", "ios")
            if name in normalize(question)
        ),
        "",
    )
    name = f"{model['brand_name']} {model['model_code']}"
    public = f"{name} {topic} {operating}".strip()
    query_key = hashlib.sha256(
        json.dumps(
            [
                public,
                domains,
                aliases,
                model.get("region"),
                model.get("hardware_revision"),
                sorted(model.get("blocked_urls") or []),
                requires_evidence(question),
                2,
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return (
        [
            f"{name} 说明书",
            f"{model['model_code']} {topic}" if domains else f"{name} user manual support",
            public + " 教程",
        ],
        query_key,
        keywords,
    )


def safe_candidate(url):
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and len(url) <= 2048
        )
    except ValueError:
        return False


def _match(model, title, body, trusted):
    aliases, _ = identity(model)
    text = body[:2500]
    match = contains(text, model["model_code"])
    if not match:
        return "wrong_model"
    if re.search(r"[a-z]+[\s_-]*\d+", normalize(title)) and not contains(
        title, model["model_code"]
    ):
        return "wrong_model"
    following = [normalize(text)[match.end() : match.end() + 20]]
    title_match = contains(title, model["model_code"])
    if title_match:
        following.append(normalize(title)[title_match.end() : title_match.end() + 20])
    variant = (
        r"^[\s_-]*(?:升级版|升级款|upgraded|generation\s*[2-9]|"
        r"(?:v[2-9]|[abc]|pro|max|ultra|plus|lite|se)(?![a-z0-9]))"
    )
    if any(re.match(variant, tail) for tail in following) and not re.search(
        r"升级|upgraded|v[2-9]", normalize(model["model_code"])
    ):
        return "wrong_model"
    for key in ("region", "hardware_revision"):
        if model.get(key) and not contains(text, model[key]):
            return "wrong_model"
    if any(contains(text, alias) for alias in aliases) or trusted:
        return "matched"
    for other, _ in BRANDS:
        if any(contains(text, alias) for alias in other):
            return "identity_conflict"
    return "wrong_model"


def _excerpt(text, keywords):
    match = re.search(keywords, text, re.I)
    if not match:
        return None
    # Center the evidence window on the question's subject, not site navigation.
    start = max(0, match.start() - 200)
    return text[start : start + 2000].strip()


def cache_valid(cached, query_key, domains):
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(cached["searchedAt"])).total_seconds()
        if cached["queryKey"] != query_key or not 0 <= age < (
            21600 if cached["status"] == "found" else 900
        ):
            return False
        return all(
            s["sourceKind"] != "official"
            or official_url(s["url"], domains)
            or (official_url(s.get("linkedFrom", ""), domains) and s.get("linkedUrl") == s["url"])
            for s in cached.get("sources", [])
        )
    except (KeyError, TypeError, ValueError):
        return False


def research(
    model, question, *, user, cancelled, deadline, cached=None, force=False, on_status=None
):
    query_list, key, keywords = queries(model, question)
    _, domains = identity(model)
    blocked = set(model.get("blocked_urls") or [])
    result = {
        "status": "empty",
        "reason": "no_results",
        "queryKey": key,
        "searchedAt": datetime.now(UTC).isoformat(),
        "sources": [],
        "cached": False,
    }
    if cancelled():
        raise DomainError(409, "RESOURCE_CONFLICT", "回答已停止")
    if risk_of(question) == "high":
        return {**result, "reason": "unsafe"}
    if (
        cached
        and not force
        and cache_valid(cached, key, domains)
        and not (
            requires_evidence(question)
            and any(s["sourceKind"] != "official" for s in cached.get("sources", []))
        )
    ):
        if on_status:
            on_status("web_cached")
        return {**cached, "cached": True}
    # Leave part of the caller's total deadline for generation and support assessment.
    deadline = min(deadline, time.monotonic() + 25)
    seen, fetched, conflict = set(), 0, None
    search = SearchGateway()
    with request_scope(deadline, cancelled):
        try:
            for index, query in enumerate(query_list):
                if time.monotonic() >= deadline:
                    result["reason"] = "timeout"
                    break
                if on_status:
                    on_status("web_searching")
                rows = search.search(
                    query,
                    timeout=min(6, deadline - time.monotonic()),
                    max_attempts=1,
                    subject=user,
                    **({"include_domains": domains} if index == 1 and domains else {}),
                )

                def priority(row):
                    title = row.get("title", "")
                    aliases, _ = identity(model)
                    return (
                        not official_url(row.get("url", ""), domains),
                        not bool(
                            re.search(
                                r"说明书|手册|教程|manual|guide|support|\.pdf",
                                title + row.get("url", ""),
                                re.I,
                            )
                        ),
                        not bool(contains(title, model["model_code"])),
                        not any(contains(title, alias) for alias in aliases),
                        bool(re.search(r"包邮|优惠|报价|价格|促销", title)),
                    )

                queue = [
                    (row, None)
                    for row in sorted((row for row in rows if isinstance(row, dict)), key=priority)
                    if isinstance(row, dict) and safe_candidate(row.get("url", ""))
                ]
                group_end = min(5, fetched + (2 if index < 2 else 5))
                while queue and fetched < group_end and time.monotonic() < deadline:
                    if cancelled():
                        raise DomainError(409, "RESOURCE_CONFLICT", "回答已停止")
                    row, parent = queue.pop(0)
                    url = urldefrag(row["url"])[0]
                    if url in seen or url in blocked:
                        continue
                    seen.add(url)
                    fetched += 1
                    if on_status:
                        on_status("web_reading")
                    try:
                        final, mime, data = fetch_public(url, maximum=8 * 1024 * 1024)
                        if not safe_candidate(final) or final in blocked:
                            continue
                        trusted = official_url(final, domains) or (
                            parent and final == url and official_url(parent, domains)
                        )
                        if mime == "text/html":
                            soup = BeautifulSoup(data[:2_000_000], "html.parser")
                            # Only a direct link from a trusted page can establish CDN provenance.
                            if trusted and not parent:
                                links = []
                                for anchor in soup.select("a[href]")[:200]:
                                    try:
                                        target = urljoin(final, anchor["href"])
                                        if safe_candidate(target) and re.search(
                                            r"\.pdf$", urlsplit(target).path, re.I
                                        ):
                                            links.append(
                                                (
                                                    {
                                                        "url": target,
                                                        "title": anchor.get_text(" ", strip=True)
                                                        or row.get("title", "官方文档"),
                                                    },
                                                    final,
                                                )
                                            )
                                    except ValueError:
                                        continue
                                queue[0:0] = links[:3]
                        pages = parse_document(data, mime)
                    except DomainError as error:
                        if (
                            error.status == 409
                            or error.status == 429
                            or error.code == "ANSWER_TIMEOUT"
                        ):
                            raise
                        result["reason"] = "unreadable"
                        continue
                    title = str(row.get("title", "资料来源"))[:200]
                    body = "\n".join(text for _, text in pages)[:80000]
                    match = _match(model, title, body, trusted)
                    if match == "identity_conflict":
                        conflict = {"title": title, "url": final}
                        continue
                    if match != "matched":
                        result["reason"] = "wrong_model"
                        continue
                    if requires_evidence(question) and not trusted:
                        result["reason"] = "official_required"
                        continue
                    if on_status:
                        on_status("web_verifying")
                    for page, text in pages:
                        excerpt = _excerpt(text, keywords)
                        if not excerpt:
                            continue
                        source = {
                            "id": str(uuid4()),
                            "rank": len(result["sources"]) + 1,
                            "title": title,
                            "url": final,
                            "sourceKind": "official"
                            if trusted
                            else (
                                "unverified_official"
                                if re.search(r"官方|官网|official", title, re.I)
                                else "third_party"
                            ),
                            "fetchedAt": datetime.now(UTC).isoformat(),
                            "contentSha256": hashlib.sha256(data).hexdigest(),
                            "excerpt": excerpt,
                            "pageStart": page if mime == "application/pdf" else None,
                            "pageEnd": page if mime == "application/pdf" else None,
                            "modelMatch": "exact",
                        }
                        if parent and trusted:
                            source.update(linkedFrom=parent, linkedUrl=final)
                        result["sources"].append(source)
                        break
                if fetched >= 5:
                    break
        except DomainError as error:
            if error.status == 409:
                raise
            result["reason"] = (
                "quota"
                if error.status == 429
                else "timeout"
                if error.code == "ANSWER_TIMEOUT"
                else "search_unavailable"
            )
    if result["sources"]:
        result.update(status="found", reason=None)
    elif conflict:
        result.update(status="identity_conflict", reason="identity_conflict", conflict=conflict)
    return result
