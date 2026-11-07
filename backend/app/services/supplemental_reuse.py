"""Revalidate frozen public manuals before any website/search request."""

from app.db import many, one, transaction
from app.errors import DomainError
from app.manufacturers import reviewed_identity_aliases
from app.security import normalize
from app.services.queue import fenced
from app.services.supplemental_index import authority
from app.services.supplemental_provenance import parent_valid
from app.storage import Storage, sha256


def frozen_pages(connection, version):
    """The network fallback enforces the same frozen-evidence integrity checks."""
    from app.services.knowledge import document_chunks

    asset = one(
        connection,
        "SELECT a.object_key,v.sha256 FROM document_versions v JOIN assets a ON a.id=v.asset_id "
        "WHERE v.id=:v AND a.scope='public' AND a.status='available'",
        v=version["id"],
    )
    try:
        valid = asset and sha256(Storage().read(asset["object_key"])) == asset["sha256"]
    except OSError:
        valid = False
    if not valid:
        raise DomainError(409, "SOURCE_UNVERIFIED", "保存的补充资料文件校验失败")
    pages = [
        (r["page_no"], r["extracted_text"])
        for r in many(
            connection,
            "SELECT page_no,extracted_text FROM document_pages "
            "WHERE document_version_id=:v ORDER BY page_no",
            v=version["id"],
        )
    ]
    expected = {n: (page, page, text) for n, page, text in document_chunks(pages)}
    chunks = many(
        connection,
        "SELECT k.chunk_no,k.page_start,k.page_end,k.content FROM supplemental_chunks k "
        "JOIN supplemental_documents d ON d.id=k.supplemental_document_id "
        "WHERE d.document_version_id=:v",
        v=version["id"],
    )
    if not pages or any(
        expected.get(r["chunk_no"]) != (r["page_start"], r["page_end"], r["content"])
        for r in chunks
    ):
        raise DomainError(409, "SOURCE_UNVERIFIED", "保存的补充资料正文或索引片段校验失败")
    return pages


def reuse(job, model):
    from app.services.knowledge import document_chunks
    from app.services.supplemental_research import (
        current_model,
        ensure_document,
        matches,
        publish,
        publisher,
    )

    with transaction() as c:
        fenced(c, job)
        versions = many(
            c,
            "SELECT v.*,s.url,a.object_key,sd.product_model_id AS original_model_id,"
            "sd.publisher_url,sd.parent_document_version_id,sd.parent_link_url,sd.page_count "
            "FROM supplemental_documents sd JOIN document_versions v "
            "ON v.id=sd.document_version_id JOIN documents d ON d.id=v.document_id "
            "JOIN sources s ON s.id=d.source_id JOIN assets a ON a.id=v.asset_id "
            "JOIN product_models m ON m.id=sd.product_model_id JOIN brands b ON b.id=m.brand_id "
            "WHERE sd.status='ready' AND m.identity_status IN ('user_confirmed','verified') "
            "AND b.normalized_name=ANY(:aliases) AND m.normalized_model_code=:code "
            "AND COALESCE(m.region,'')=:region AND COALESCE(m.hardware_revision,'')=:hardware "
            "AND v.parse_status='parsed' AND v.verification_status NOT IN ('revoked','rejected') "
            "AND s.verification_status NOT IN ('revoked','rejected') AND NOT s.is_official "
            "AND s.source_type='community' AND a.scope='public' AND a.status='available' "
            "ORDER BY (sd.product_model_id=:model) DESC,sd.updated_at DESC LIMIT 6",
            aliases=list(reviewed_identity_aliases(model["brand_name"])),
            code=normalize(model["model_code"]),
            region=model["region"] or "",
            hardware=model["hardware_revision"] or "",
            model=model["id"],
        )
    published, seen = 0, set()
    for version in versions:
        if published >= 2:
            break
        if version["id"] in seen or not publisher(version["url"]):
            continue
        seen.add(version["id"])
        try:
            with transaction() as c:
                # Re-check the original model's source and parent provenance under locks.
                authority(c, job, version["original_model_id"], version["id"])
                if not parent_valid(c, version, lock=True):
                    continue
                pages = frozen_pages(c, version)
                if not pages or len(pages) != version["page_count"]:
                    continue
                original = many(
                    c,
                    "SELECT k.chunk_no,k.page_start,k.page_end,k.content "
                    "FROM supplemental_chunks k JOIN supplemental_documents sd "
                    "ON sd.id=k.supplemental_document_id WHERE sd.document_version_id=:v "
                    "AND sd.product_model_id=:m ORDER BY k.chunk_no",
                    v=version["id"],
                    m=version["original_model_id"],
                )
                expected = [(n, p, p, text) for n, p, text in document_chunks(pages)]
                if [
                    (r["chunk_no"], r["page_start"], r["page_end"], r["content"]) for r in original
                ] != expected:
                    continue
                current = current_model(c, model["id"])
                if not matches(current, pages):
                    continue
                ensure_document(
                    c,
                    current,
                    version,
                    version["publisher_url"],
                    version["parent_document_version_id"],
                    version["parent_link_url"],
                )
                authority(c, job, current["id"], version["id"])
            if publish(job, current, version, pages, version["publisher_url"]):
                published += 1
        except OSError:
            continue
        except DomainError as error:
            if error.code not in {"SOURCE_UNVERIFIED", "FILE_TOO_LARGE"}:
                raise
    return published
