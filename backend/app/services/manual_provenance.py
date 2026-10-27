"""Recheck a stored manufacturer page's exact PDF link before public publication."""

from app.db import one
from app.errors import DomainError
from app.services.source_discovery import manual_links
from app.services.web_research import official_url
from app.storage import Storage, sha256


def linked_manual_is_official(connection, document, *, lock=False):
    parent_id = document.get("official_parent_version_id")
    if not parent_id or document.get("official_link_url") != document["url"]:
        return False
    parent = one(
        connection,
        "SELECT v.*,a.object_key,s.url,s.verification_status AS source_status "
        "FROM document_versions v JOIN assets a ON a.id=v.asset_id "
        "JOIN documents d ON d.id=v.document_id JOIN sources s ON s.id=d.source_id "
        "WHERE v.id=:id" + (" FOR SHARE OF v,s" if lock else ""),
        id=parent_id,
    )
    domains = document["official_domains"] or []
    if (
        not parent
        or parent["mime_type"] != "text/html"
        or parent["official_parent_version_id"]
        or parent["verification_status"] in {"revoked", "rejected"}
        or parent["source_status"] in {"revoked", "rejected"}
        or not official_url(parent["url"], domains)
    ):
        return False
    try:
        raw = Storage().read(parent["object_key"])
    except (OSError, DomainError):
        return False
    return sha256(raw) == parent["sha256"] and any(
        link["url"] == document["url"]
        for link in manual_links(
            parent["url"], raw, domains, include_linked=True, model_code=document.get("model_code")
        )
    )
