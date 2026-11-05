"""Non-official reference index. Never consulted by manufacturer manuals or guidance."""
# ruff: noqa: S608
# SQL fragments below are fixed module constants; every runtime value is bound.

from datetime import UTC, datetime
from hashlib import sha256
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


def context_window(row, before=None, after=None, *, page_text):
    """Repair boundary lines using immutable neighbors; never publish a clipped row."""

    def adjacent(other, offset):
        return (
            other
            and other["chunk_no"] == row["chunk_no"] + offset
            and all(
                other[key] == row[key]
                for key in (
                    "supplemental_document_id",
                    "document_version_id",
                    "page_start",
                    "page_end",
                    "embedding_model",
                    "embedding_revision",
                )
            )
            and (
                (
                    other["content"] + row["content"]
                    if offset < 0
                    else row["content"] + other["content"]
                )
                in page_text
            )
        )

    text = row["content"]
    used = [row]
    if adjacent(before, -1) and not before["content"].endswith("\n") and not text.startswith("\n"):
        prefix = before["content"].rsplit("\n", 1)[-1]
        # A line longer than a complete chunk cannot be reconstructed within the
        # evidence budget. Drop it rather than exposing a fragment as a full row.
        if "\n" in before["content"] or len(before["content"]) < 2000:
            text = prefix + text
            used.append(before)
        else:
            text = text.partition("\n")[2]
    elif not page_text.startswith(text) and not text.startswith("\n"):
        text = text.partition("\n")[2]
    if not text:
        return "", used
    if adjacent(after, 1) and not row["content"].endswith("\n"):
        suffix = after["content"].split("\n", 1)[0]
        text += suffix
        used.append(after)
        if "\n" not in after["content"] and len(after["content"]) == 2000:
            text = text.rpartition("\n")[0]
    elif not page_text.endswith(row["content"]) and not text.endswith("\n"):
        text = text.rpartition("\n")[0]
    while len(text) > 2000:
        text = text.partition("\n")[2]
    return (text, used) if text in page_text else ("", used)


def contextual_rows(connection, model_id, selected):
    """Fetch at most two neighbors per hit, independently of retrieval ranking."""
    rows = many(
        connection,
        "SELECT " + FIELDS + " " + ELIGIBLE + " AND EXISTS (SELECT 1 FROM "
        "supplemental_chunks anchor WHERE anchor.id=ANY(:ids) AND "
        "anchor.supplemental_document_id=k.supplemental_document_id AND "
        "k.chunk_no BETWEEN anchor.chunk_no-1 AND anchor.chunk_no+1) ORDER BY k.id LIMIT 30",
        **parameters(model_id),
        ids=[r["id"] for r in selected],
    )
    rows = [r for r in rows if parent_valid(connection, r)]
    by_id = {r["id"]: r for r in rows}
    by_position = {(r["supplemental_document_id"], r["chunk_no"]): r for r in rows}
    result = []
    pages = {}
    for hit in selected:
        row = by_id.get(hit["id"])
        if not row:
            continue
        page_key = row["document_version_id"], row["page_start"]
        if page_key not in pages:
            pages[page_key] = one(
                connection,
                "SELECT extracted_text FROM document_pages "
                "WHERE document_version_id=:v AND page_no=:p",
                v=page_key[0],
                p=page_key[1],
            )
        page = pages[page_key]
        if not page:
            continue
        page_text = page["extracted_text"]
        key, number = row["supplemental_document_id"], row["chunk_no"]
        excerpt, contributors = context_window(
            row,
            by_position.get((key, number - 1)),
            by_position.get((key, number + 1)),
            page_text=page_text,
        )
        if excerpt.strip():
            page_proof = {"sha256": sha256(page_text.encode()).hexdigest(), "excerpt": excerpt}
            result.append((row, excerpt, contributors, page_proof))
    return result


def proof(row):
    return {
        k: str(row[k]) if k in {"id", "document_version_id"} else row[k]
        for k in ("id", "content", "document_version_id", "page_start", "page_end", "sha256", "url")
    }


def proof_rows(values):
    """Old single-chunk snapshots and new overlapping contexts share validation."""
    result = {}
    for value in values:
        for item in [value, *value.get("context", [])]:
            row = {k: v for k, v in item.items() if k not in {"context", "page"}}
            row.update(id=UUID(row["id"]), document_version_id=UUID(row["document_version_id"]))
            if row["id"] in result and result[row["id"]] != row:
                raise DomainError(409, "SOURCE_UNVERIFIED", "补充资料上下文不一致，请重新提问")
            result[row["id"]] = row
    return list(result.values())


def page_valid(connection, value):
    if "page" not in value:
        return True  # Previously saved single-chunk citations remain compatible.
    page = one(
        connection,
        "SELECT extracted_text FROM document_pages WHERE document_version_id=:v AND page_no=:p",
        v=UUID(value["document_version_id"]),
        p=value["page_start"],
    )
    expected = value["page"]
    return bool(
        page
        and value["page_start"] == value["page_end"]
        and sha256(page["extracted_text"].encode()).hexdigest() == expected["sha256"]
        and expected["excerpt"] in page["extracted_text"]
    )


def lookup(model, question, gateway, subject):
    from app.operations import query_vector
    from app.services.web_research import queries

    with transaction() as c:
        if not documents(c, model["id"]):
            return None
    rows = retrieve(model["id"], question, query_vector(gateway, question, subject))
    if not rows:
        return None
    with transaction() as c:
        contexts = contextual_rows(c, model["id"], rows)
    if not contexts:
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
                "excerpt": excerpt,
                "pageStart": row["page_start"],
                "pageEnd": row["page_end"],
                "modelMatch": "exact",
                "_supplemental": {
                    **proof(row),
                    "context": [proof(r) for r in contributors if r["id"] != row["id"]],
                    "page": page_proof,
                },
            }
            for i, (row, excerpt, contributors, page_proof) in enumerate(contexts, 1)
        ],
    }


def validate_lookup(connection, model_id, lookup_value):
    if not lookup_value or lookup_value.get("lookupMode") != "supplemental_index":
        return
    selected = proof_rows([s["_supplemental"] for s in lookup_value.get("sources", [])])
    if selected and (
        not live(connection, model_id, selected)
        or not all(page_valid(connection, s["_supplemental"]) for s in lookup_value["sources"])
    ):
        raise DomainError(409, "SOURCE_UNVERIFIED", "补充资料已变更或撤销，请重新提问")


def availability(connection, source):
    value = source.get("_supplemental")
    if not value:
        return source
    selected = proof_rows([value])
    model = one(
        connection,
        "SELECT d.product_model_id FROM supplemental_chunks k JOIN supplemental_documents d "
        "ON d.id=k.supplemental_document_id WHERE k.id=:id",
        id=UUID(value["id"]),
    )
    return {
        **source,
        "availabilityStatus": "available"
        if model
        and live(connection, model["product_model_id"], selected)
        and page_valid(connection, value)
        else "withdrawn",
    }
