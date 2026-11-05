"""Non-official reference index. Never consulted by manufacturer manuals or guidance."""
# ruff: noqa: S608
# SQL fragments below are fixed module constants; every runtime value is bound.

from datetime import UTC, datetime
from uuid import UUID

from app.config import settings
from app.db import json_value, many, one, transaction
from app.errors import DomainError
from app.services.queue import fenced
from app.services.supplemental_provenance import parent_valid

# Raw third-party versions remain pending in the official evidence tables.
ELIGIBLE = """
FROM supplemental_chunks k JOIN supplemental_documents d ON d.id=k.supplemental_document_id
JOIN document_versions v ON v.id=d.document_version_id
JOIN documents doc ON doc.id=v.document_id JOIN sources s ON s.id=doc.source_id
JOIN assets a ON a.id=v.asset_id JOIN product_models m ON m.id=d.product_model_id
WHERE d.product_model_id=:model AND d.status='ready'
AND m.identity_status IN ('user_confirmed','verified')
AND v.verification_status NOT IN ('rejected','revoked')
AND s.verification_status NOT IN ('rejected','revoked') AND NOT s.is_official
AND s.source_type='community' AND a.scope='public' AND a.status='available'
AND k.embedding_model=:embedding_model AND k.embedding_revision=:revision
AND (d.parent_document_version_id IS NULL OR EXISTS (
 SELECT 1 FROM document_versions pv JOIN documents pd ON pd.id=pv.document_id
 JOIN sources ps ON ps.id=pd.source_id JOIN assets pa ON pa.id=pv.asset_id
 WHERE pv.id=d.parent_document_version_id AND pv.mime_type='text/html'
 AND pv.verification_status NOT IN ('revoked','rejected')
 AND ps.verification_status NOT IN ('revoked','rejected') AND NOT ps.is_official
 AND ps.source_type='community' AND pa.scope='public' AND pa.status='available'))
"""
FIELDS = (
    "k.*,d.product_model_id,d.document_version_id,d.publisher_url,"
    "d.parent_document_version_id,d.parent_link_url,doc.title,s.url,"
    "v.sha256,v.mime_type,v.fetched_at"
)


def authority(connection, job, model_id, version_id):
    fenced(connection, job)
    row = one(
        connection,
        "SELECT v.*,s.url,s.id AS source_id FROM document_versions v JOIN documents d "
        "ON d.id=v.document_id JOIN sources s ON s.id=d.source_id JOIN assets a "
        "ON a.id=v.asset_id JOIN product_models m ON m.id=:m WHERE v.id=:v "
        "AND m.identity_status IN ('user_confirmed','verified') AND "
        "v.verification_status NOT IN ('revoked','rejected') AND "
        "s.verification_status NOT IN ('revoked','rejected') AND NOT s.is_official "
        "AND s.source_type='community' AND a.scope='public' AND a.status='available' "
        "FOR SHARE OF v,s,a,m",
        m=model_id,
        v=version_id,
    )
    if not row:
        raise DomainError(409, "SOURCE_UNVERIFIED", "补充资料来源已撤销或不再适用于该型号")
    supplemental = one(
        connection,
        "SELECT * FROM supplemental_documents WHERE "
        "document_version_id=:v AND product_model_id=:m FOR SHARE",
        v=version_id,
        m=model_id,
    )
    if supplemental and supplemental["status"] in {"revoked", "rejected"}:
        raise DomainError(409, "SOURCE_UNVERIFIED", "补充资料已撤销或拒绝")
    if supplemental and not parent_valid(connection, {**supplemental, **row}, lock=True):
        raise DomainError(409, "SOURCE_UNVERIFIED", "补充说明书的原发布页或下载链接已失效")
    return row


def parameters(model_id):
    return dict(
        model=model_id,
        embedding_model=settings().embedding_model,
        revision=settings().embedding_revision,
    )


def retrieve(model_id, question, vector, limit=6):
    with transaction() as c:
        # Bound both retrieval paths before their final fusion. Identity, source and
        # embedding revision are checked inside each candidate relation.
        rows = many(
            c,
            "WITH eligible AS MATERIALIZED (SELECT " + FIELDS + " " + ELIGIBLE + "), "
            "semantic AS (SELECT id,row_number() OVER(ORDER BY embedding <=> "
            "CAST(:vector AS vector),id) AS rank "
            "FROM (SELECT * FROM eligible ORDER BY embedding <=> CAST(:vector AS vector) "
            "LIMIT 30) v), "
            "lexical AS (SELECT id,row_number() OVER(ORDER BY "
            "word_similarity(:q,content) DESC,id) AS rank "
            "FROM (SELECT * FROM eligible WHERE word_similarity(:q,content)>0.1 "
            "ORDER BY word_similarity(:q,content) DESC LIMIT 30) t), "
            "ranked AS (SELECT COALESCE(s.id,l.id) AS id,COALESCE(1.0/(60+s.rank),0)+"
            "COALESCE(1.0/(60+l.rank),0) AS score FROM semantic s FULL JOIN lexical l ON "
            "s.id=l.id) "
            "SELECT e.* FROM ranked r JOIN eligible e ON e.id=r.id ORDER BY r.score "
            "DESC,r.id LIMIT :limit",
            **parameters(model_id),
            q=question,
            vector=json_value(vector),
            limit=max(1, min(limit, 10)),
        )
        return [row for row in rows if parent_valid(c, row)]


def live(connection, model_id, selected):
    if not selected:
        return []
    rows = many(
        connection,
        "SELECT " + FIELDS + " " + ELIGIBLE + " AND k.id=ANY(:ids) FOR SHARE OF k,d,v,s,a,m",
        **parameters(model_id),
        ids=[row["id"] for row in selected],
    )
    fresh = {row["id"]: row for row in rows}
    if any(not parent_valid(connection, row, lock=True) for row in rows):
        return []
    if len(fresh) != len(selected) or any(
        row["id"] not in fresh
        or any(
            fresh[row["id"]][key] != row[key]
            for key in ("content", "document_version_id", "page_start", "page_end", "sha256", "url")
        )
        for row in selected
    ):
        return []
    return [fresh[row["id"]] for row in selected]


def documents(connection, model_id):
    rows = many(
        connection,
        "SELECT DISTINCT d.id,doc.title,s.url,v.fetched_at,d.page_count,v.mime_type,"
        "d.parent_document_version_id,d.parent_link_url,d.publisher_url,"
        "(SELECT count(*) FROM supplemental_chunks c WHERE c.supplemental_document_id=d.id "
        "AND c.embedding_model=:embedding_model AND c.embedding_revision=:revision) AS chunk_count "
        + ELIGIBLE
        + " ORDER BY d.id",
        **parameters(model_id),
    )
    return [
        dict(
            id=str(r["id"]),
            title=r["title"],
            url=r["url"],
            sourceKind="third_party",
            fetchedAt=r["fetched_at"].isoformat(),
            pageCount=r["page_count"],
            chunkCount=r["chunk_count"],
            matchStatus="exact",
            availabilityStatus="available",
        )
        for r in rows
        if parent_valid(connection, r)
    ]


def lookup(model, question, gateway, subject):
    from app.operations import query_vector
    from app.services.web_research import queries

    with transaction() as c:
        if not documents(c, model["id"]):
            return None
    rows = retrieve(model["id"], question, query_vector(gateway, question, subject))
    if not rows:
        return None
    return {
        "status": "found",
        "reason": None,
        "queryKey": queries(model, question)[1],
        "searchedAt": datetime.now(UTC).isoformat(),
        "cached": False,
        "lookupMode": "supplemental_index",
        "sources": [
            {
                "id": str(row["id"]),
                "rank": i,
                "title": row["title"],
                "url": row["url"],
                "sourceKind": "third_party",
                "fetchedAt": row["fetched_at"].isoformat(),
                "contentSha256": row["sha256"],
                "excerpt": row["content"],
                "pageStart": row["page_start"],
                "pageEnd": row["page_end"],
                "modelMatch": "exact",
                "_supplemental": {
                    k: str(row[k]) if k in {"id", "document_version_id"} else row[k]
                    for k in (
                        "id",
                        "content",
                        "document_version_id",
                        "page_start",
                        "page_end",
                        "sha256",
                        "url",
                    )
                },
            }
            for i, row in enumerate(rows, 1)
        ],
    }


def validate_lookup(connection, model_id, lookup_value):
    if not lookup_value or lookup_value.get("lookupMode") != "supplemental_index":
        return
    selected = []
    for source in lookup_value.get("sources", []):
        row = dict(source["_supplemental"])
        row.update(id=UUID(row["id"]), document_version_id=UUID(row["document_version_id"]))
        selected.append(row)
    if selected and not live(connection, model_id, selected):
        raise DomainError(409, "SOURCE_UNVERIFIED", "补充资料已变更或撤销，请重新提问")


def availability(connection, source):
    value = source.get("_supplemental")
    if not value:
        return source
    row = dict(value)
    row.update(id=UUID(row["id"]), document_version_id=UUID(row["document_version_id"]))
    model = one(
        connection,
        "SELECT d.product_model_id FROM supplemental_chunks k JOIN supplemental_documents d "
        "ON d.id=k.supplemental_document_id WHERE k.id=:id",
        id=row["id"],
    )
    return {
        **source,
        "availabilityStatus": "available"
        if model and live(connection, model["product_model_id"], [row])
        else "withdrawn",
    }
