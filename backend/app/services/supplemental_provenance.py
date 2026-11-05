"""A publisher's direct PDF link stays bound to its immutable parent version."""
# ruff: noqa: S608
# The optional lock clause is a fixed literal selected by a boolean, never input.

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from app.db import one
from app.errors import DomainError
from app.storage import Storage, sha256


def parent_valid(connection, document, *, lock=False):
    parent_id = document.get("parent_document_version_id")
    if not parent_id:
        return True
    if (
        document.get("mime_type") != "application/pdf"
        or document.get("parent_link_url") != document["url"]
    ):
        return False
    parent = one(
        connection,
        "SELECT v.*,a.object_key,s.url FROM document_versions v "
        "JOIN documents d ON d.id=v.document_id JOIN sources s ON s.id=d.source_id "
        "JOIN assets a ON a.id=v.asset_id WHERE v.id=:id AND v.mime_type='text/html' "
        "AND v.verification_status NOT IN ('revoked','rejected') "
        "AND s.verification_status NOT IN ('revoked','rejected') "
        "AND NOT s.is_official AND s.source_type='community' "
        "AND a.scope='public' AND a.status='available'" + (" FOR SHARE OF v,s,a" if lock else ""),
        id=parent_id,
    )
    if not parent or parent["url"] != document["publisher_url"]:
        return False
    from app.services.supplemental_research import publisher

    if not publisher(parent["url"]) or not publisher(document["url"]):
        return False
    try:
        data = Storage().read(parent["object_key"])
    except (OSError, DomainError):
        return False
    return sha256(data) == parent["sha256"] and any(
        urldefrag(urljoin(parent["url"], tag["href"]))[0] == document["parent_link_url"]
        for tag in BeautifulSoup(data[:2_000_000], "html.parser").select("a[href]")
    )
