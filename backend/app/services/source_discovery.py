"""Bounded official candidates and one-level download links, without new trust grants."""

import re
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.errors import DomainError


def allowed_url(url, domains):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return (
            parts.scheme == "https"
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
            and any(host == domain or host.endswith("." + domain) for domain in domains)
        )
    except ValueError:
        return False


def discover_candidates(model, known, search):
    domains = model["official_domains"] or []
    results = {}

    def compact(value):
        return "".join(c for c in value.casefold() if c.isalnum())

    code = compact(model["model_code"])

    def exact(row):
        return bool(code and code in compact(row["url"] + " " + row.get("title", "")))

    def rank(row):
        return (not exact(row), not bool(re.search(r"\.pdf$", urlsplit(row["url"]).path, re.I)))

    def add(rows):
        for row in rows:
            if allowed_url(row["url"], domains):
                results.setdefault(urldefrag(row["url"])[0], row)

    add(known)
    registered = set(results)
    if not domains:
        return list(results.values())[:10]
    name = f"{model['brand_name']} {model['model_code']}"
    queries = [
        f'"{model["model_code"]}" {model["brand_name"]} {model["region"] or ""} 官方 使用手册',
        f"{name} user manual support",
        f"site:{domains[0]} {model['model_code']}",
    ]
    for query in queries:
        previous_count = len(results)
        previous_urls = set(results)
        try:
            add(search(query))
        except DomainError as error:
            if error.status == 429 or error.code != "UPSTREAM_SEARCH_ERROR":
                raise
            # Another wording is not a retry of a failed provider request.
            # Do not amplify an outage; keep registered-source path independent.
            break
        if len(results) > previous_count and any(
            key not in previous_urls
            and exact(row)
            and re.search(r"\.pdf$", urlsplit(row["url"]).path, re.I)
            for key, row in results.items()
        ):
            break
    old = sorted([row for key, row in results.items() if key in registered], key=rank)
    new = sorted([row for key, row in results.items() if key not in registered], key=rank)
    # Reserve space for both paths; a full stale registry cannot starve replacements.
    selected = (old[:5] + new[:5] + old[5:] + new[5:])[:10]
    return sorted(selected, key=rank)


def _add_ranked(result, target, title, model_code, order, limit):
    def compact(value):
        return "".join(c for c in value.casefold() if c.isalnum())

    model = compact(model_code or "")
    score = 0 if model and model in compact(target + " " + title) else 1
    previous = result.get(target)
    if previous and previous[0] <= score:
        return
    result[target] = (score, order, {"url": target, "title": title})
    if len(result) > limit:
        result.pop(max(result, key=lambda key: result[key][:2]))


def support_links(url, data, domains, *, model_code=None):
    """One manufacturer support-page hop; no arbitrary navigation crawl."""
    from app.services.web_research import official_url

    if not official_url(url, domains):
        return []
    soup = BeautifulSoup(data[:2_000_000], "html.parser")
    result = {}
    for order, tag in enumerate(soup.select("a[href]")):
        try:
            target = urldefrag(urljoin(url, tag["href"]))[0]
            path = urlsplit(target).path
        except ValueError:
            continue
        label = tag.get_text(" ", strip=True)[:200]
        if (
            target != url
            and len(target) <= 2048
            and official_url(target, domains)
            and not re.search(r"\.(?:pdf|exe|zip|dmg|msi)$", path, re.I)
            and re.search(
                r"support|download|manual|help|guide|说明书|支持|下载", path + " " + label, re.I
            )
        ):
            _add_ranked(result, target, label or "官方支持", model_code, order, 3)
    return [item for _, _, item in sorted(result.values())]


def manual_links(url, data, domains, *, include_linked=False, model_code=None):
    from app.services.web_research import official_url

    if include_linked and not official_url(url, domains):
        return []
    soup = BeautifulSoup(data[:2_000_000], "html.parser")
    links = {}
    for order, tag in enumerate(soup.select("a[href]")):
        try:
            target = urldefrag(urljoin(url, tag["href"]))[0]
        except ValueError:
            continue
        if len(target) > 2048:
            continue
        safe = allowed_url(target, domains)
        if include_linked:
            from app.services.recognition_sources import safe_reference_url

            safe = safe_reference_url(target)
        if safe and re.search(r"\.pdf$", urlsplit(target).path, re.I):
            title = tag.get_text(" ", strip=True)[:200] or "官方说明书"
            _add_ranked(links, target, title, model_code, order, 5)
    return [item for _, _, item in sorted(links.values())]
