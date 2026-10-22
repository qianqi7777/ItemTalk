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
        f"{name} {model['region'] or ''} 官方 使用手册",
        f"{name} user manual support",
        f"site:{domains[0]} {model['model_code']}",
    ]
    for query in queries:
        previous_count = len(results)
        try:
            add(search(query))
        except DomainError as error:
            if error.status == 429 or error.code != "UPSTREAM_SEARCH_ERROR":
                raise
            # Another wording is not a retry of a failed provider request.
            # Do not amplify an outage; keep registered-source path independent.
            break
        if len(results) > previous_count:
            break
    old = [row for key, row in results.items() if key in registered]
    new = [row for key, row in results.items() if key not in registered]
    # Reserve space for both paths; a full stale registry cannot starve replacements.
    return (old[:5] + new[:5] + old[5:] + new[5:])[:10]


def manual_links(url, data, domains):
    soup = BeautifulSoup(data[:2_000_000], "html.parser")
    links = {}
    for tag in soup.select("a[href]")[:200]:
        try:
            target = urldefrag(urljoin(url, tag["href"]))[0]
        except ValueError:
            continue
        if allowed_url(target, domains) and re.search(r"\.pdf$", urlsplit(target).path, re.I):
            links.setdefault(
                target,
                {"url": target, "title": tag.get_text(" ", strip=True)[:200] or "官方说明书"},
            )
        if len(links) == 5:
            break
    return list(links.values())
