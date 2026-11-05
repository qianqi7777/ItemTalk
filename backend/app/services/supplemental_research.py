"""Bounded publisher-manual ingestion, with no manufacturer trust grant."""

import re
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.adapters import ModelGateway, SearchGateway
from app.config import settings
from app.db import execute, json_value, many, one, transaction
from app.errors import DomainError
from app.fetch import fetch_public
from app.parser import parse_document
from app.security import normalize
from app.services.document_extraction import extract_scanned_pages
from app.services.queue import fenced, progress
from app.services.recognition_sources import safe_reference_url, supports_model
from app.services.supplemental_index import authority
from app.services.web_research import contains

PUBLISHERS = ("manuals.plus", "manualslib.com", "manua.ls", "device.report", "fccid.io")


def publisher(url):
    if not safe_reference_url(url):
        return False
    host = urlsplit(url).hostname or ""
    return any(host == domain or host.endswith("." + domain) for domain in PUBLISHERS)


def candidate(row, model):
    url = row["url"]
    path = unquote(urlsplit(url).path)
    return bool(
        publisher(url)
        and not re.search(r"/(?:search|categories|category|collections?)(?:[/.]|$)", path, re.I)
        and re.search(r"manual|guide|instruction|\.pdf$", path, re.I)
        and supports_model(
            model["brand_name"], model["model_code"], row.get("title", "") + " " + path
        )
    )


def discover(model):
    with transaction() as c:
        known = many(
            c,
            "SELECT s.url,d.title FROM sources s JOIN documents d ON d.source_id=s.id "
            "WHERE d.logical_key=:key AND s.source_type='community' AND "
            "s.verification_status NOT IN ('revoked','rejected') ORDER BY d.created_at LIMIT 6",
            key="supplemental-model:" + str(model["id"]),
        )
    rows = {r["url"]: r for r in known if candidate(r, model)}
    compact_model = re.sub(r"[\s_-]", "", model["model_code"])
    gateway = SearchGateway()
    try:
        for query in (
            f"{model['brand_name']} {model['model_code']} user manual pdf",
            f"{model['brand_name']} {compact_model} instruction manual",
        ):
            try:
                found = gateway.search(query, max_attempts=1, include_domains=list(PUBLISHERS))
            except DomainError as error:
                if error.status == 429 or error.code != "UPSTREAM_SEARCH_ERROR":
                    raise
                break
            rows.update({r["url"]: r for r in found if candidate(r, model)})
            if len(rows) >= 4:
                break
    finally:
        gateway.client.close()
    return sorted(
        rows.values(), key=lambda r: not urlsplit(r["url"]).path.lower().endswith(".pdf")
    )[:6]


def register(connection, model, url, title):
    """Controlled operations may register a candidate; its body still needs matching."""
    row = {"url": url, "title": title}
    if not candidate(row, model):
        raise DomainError(422, "SOURCE_UNVERIFIED", "补充资料必须是受支持发布站的具体型号说明书")
    from app.fetch import public_addresses

    public_addresses(url)
    source = one(
        connection,
        "INSERT INTO sources(url,normalized_url,domain,source_type,is_official,"
        "verification_status) VALUES(:u,:u,:host,'community',false,'pending') "
        "ON CONFLICT(normalized_url) DO UPDATE SET "
        "normalized_url=EXCLUDED.normalized_url RETURNING *",
        u=url,
        host=urlsplit(url).hostname,
    )
    if source["verification_status"] in {"revoked", "rejected"} or source["is_official"]:
        raise DomainError(409, "SOURCE_UNVERIFIED", "该来源不能登记为第三方补充资料")
    execute(
        connection,
        "INSERT INTO documents(source_id,logical_key,title,document_type) "
        "VALUES(:s,:key,:t,'manual') ON CONFLICT(source_id,logical_key) DO NOTHING",
        s=source["id"],
        key="supplemental-model:" + str(model["id"]),
        t=title[:1000],
    )
    return source


def matches(model, pages, title=""):
    from app.services.knowledge import applicable

    text = "\n".join(t for _, t in pages)
    # Never combine a search title with body text to invent a product match.
    if not supports_model(model["brand_name"], model["model_code"], text) or not applicable(
        text, model
    ):
        return False
    variant = (
        r"^[\s_-]*(?:[0-9]{1,2}(?=\s|$|[,.;:])|升级版|升级款|upgraded|"
        r"generation\s*[2-9]|(?:v[2-9]|[abc]|pro|max|ultra|plus|lite|se)(?![a-z0-9]))"
    )
    for value in (text, title):
        found = contains(value, model["model_code"])
        if found and re.match(variant, normalize(value)[found.end() : found.end() + 30]):
            return False
    return (
        not title
        or not contains(title, model["model_code"])
        or supports_model("", model["model_code"], title, trusted_brand=True)
    )


def current_model(connection, model_id):
    return one(
        connection,
        "SELECT m.*,b.name AS brand_name,b.official_domains FROM product_models m "
        "JOIN brands b ON b.id=m.brand_id WHERE m.id=:id FOR SHARE OF m,b",
        id=model_id,
    )


def pages_for(job, model, version, data, mime):
    with transaction() as c:
        authority(c, job, model["id"], version["id"])
        existing = one(
            c,
            "SELECT d.id FROM supplemental_documents d WHERE d.document_version_id=:v "
            "AND (d.status='ready' OR EXISTS(SELECT 1 FROM supplemental_chunks k "
            "WHERE k.supplemental_document_id=d.id)) LIMIT 1",
            v=version["id"],
        )
        if existing:
            return [
                (r["page_no"], r["extracted_text"])
                for r in many(
                    c,
                    "SELECT page_no,extracted_text FROM document_pages WHERE "
                    "document_version_id=:v ORDER BY page_no",
                    v=version["id"],
                )
            ]
    scans = []
    parse_data = data
    if mime == "text/html":
        soup = BeautifulSoup(data, "html.parser")
        for element in soup.select("aside,nav,header,footer,.related,.related-posts,.sidebar"):
            element.decompose()
        main = soup.select_one("article") or soup.select_one("main") or soup
        parse_data = str(main).encode()
    pages = parse_document(parse_data, mime, allow_empty_pdf=True, scan_pages=scans)
    if scans:
        pages = extract_scanned_pages(
            job,
            version,
            data,
            pages,
            scans,
            lambda c: authority(c, job, model["id"], version["id"]),
            detail_first_page=True,
        )
    return pages


def ensure_document(connection, model, version, publisher_url, parent_id=None, parent_link=None):
    return one(
        connection,
        "INSERT INTO supplemental_documents(document_version_id,product_model_id,publisher_url,"
        "parent_document_version_id,parent_link_url,status) "
        "VALUES(:v,:m,:url,:parent,:link,'processing') "
        "ON CONFLICT(document_version_id,product_model_id) DO UPDATE "
        "SET document_version_id=EXCLUDED.document_version_id RETURNING *",
        v=version["id"],
        m=model["id"],
        url=publisher_url,
        parent=parent_id,
        link=parent_link,
    )


def publish(job, model, version, pages, publisher_url):
    from app.services.knowledge import document_chunks

    pieces = document_chunks(pages)
    if not pieces:
        return False
    with transaction() as c:
        authority(c, job, model["id"], version["id"])
        if not matches(current_model(c, model["id"]), pages):
            return False
        doc = ensure_document(c, model, version, publisher_url)
        if doc["status"] in {"revoked", "rejected"}:
            return False
        previous = many(
            c, "SELECT * FROM supplemental_chunks WHERE supplemental_document_id=:d", d=doc["id"]
        )
        by_number = {number: (page, text) for number, page, text in pieces}
        if any(
            r["chunk_no"] not in by_number
            or by_number[r["chunk_no"]] != (r["page_start"], r["content"])
            or r["page_end"] != r["page_start"]
            for r in previous
        ):
            raise DomainError(409, "SOURCE_UNVERIFIED", "补充资料正文或片段与已冻结版本不同")
        existing = {
            r["chunk_no"]
            for r in previous
            if r["embedding_model"] == settings().embedding_model
            and r["embedding_revision"] == settings().embedding_revision
        }
        if len(existing) != len(pieces):
            execute(
                c,
                "UPDATE supplemental_documents SET status='processing',page_count=:n WHERE id=:id",
                id=doc["id"],
                n=len(pages),
            )
        # Freeze all pages before any paid vector batch.
        for page, text in pages:
            stored = one(
                c,
                "SELECT extracted_text FROM document_pages WHERE document_version_id=:v "
                "AND page_no=:p",
                v=version["id"],
                p=page,
            )
            if stored and stored["extracted_text"] != text:
                raise DomainError(409, "SOURCE_UNVERIFIED", "补充资料正文已冻结，不能重写")
            execute(
                c,
                "INSERT INTO document_pages(document_version_id,page_no,extracted_text) "
                "VALUES(:v,:p,:t) "
                "ON CONFLICT(document_version_id,page_no) DO NOTHING",
                v=version["id"],
                p=page,
                t=text,
            )
    gateway = ModelGateway()
    try:
        missing = [piece for piece in pieces if piece[0] not in existing]
        for offset in range(0, len(missing), 10):
            batch = missing[offset : offset + 10]
            with transaction() as c:
                authority(c, job, model["id"], version["id"])
            vectors = gateway.embed([text for _, _, text in batch], job["user_id"])
            if len(vectors) != len(batch):
                raise DomainError(502, "UPSTREAM_MODEL_ERROR", "补充资料向量数量不匹配")
            with transaction() as c:
                authority(c, job, model["id"], version["id"])
                if not matches(current_model(c, model["id"]), pages):
                    raise DomainError(409, "SOURCE_UNVERIFIED", "型号适用性已变化")
                for (number, page, text), vector in zip(batch, vectors, strict=True):
                    execute(
                        c,
                        "INSERT INTO "
                        "supplemental_chunks(supplemental_document_id,chunk_no,page_start,page_end,"
                        "content,embedding,embedding_model,embedding_revision) "
                        "VALUES(:d,:n,:p,:p,:t,CAST(:vector AS vector),:model,:rev) "
                        "ON CONFLICT(supplemental_document_id,chunk_no) DO UPDATE SET "
                        "embedding=EXCLUDED.embedding,"
                        "embedding_model=EXCLUDED.embedding_model,embedding_revision=EXCLUDED.embedding_revision",
                        d=doc["id"],
                        n=number,
                        p=page,
                        t=text,
                        vector=json_value(vector),
                        model=settings().embedding_model,
                        rev=settings().embedding_revision,
                    )
        with transaction() as c:
            authority(c, job, model["id"], version["id"])
            if not matches(current_model(c, model["id"]), pages):
                raise DomainError(409, "SOURCE_UNVERIFIED", "型号适用性已变化")
            active = one(
                c,
                "SELECT count(*) AS n FROM supplemental_chunks WHERE supplemental_document_id=:d "
                "AND embedding_model=:model AND embedding_revision=:rev",
                d=doc["id"],
                model=settings().embedding_model,
                rev=settings().embedding_revision,
            )["n"]
            if active != len(pieces):
                raise DomainError(409, "SOURCE_UNVERIFIED", "补充资料尚未完整建立索引")
            for page, text in pages:
                execute(
                    c,
                    "INSERT INTO "
                    "document_pages(document_version_id,page_no,extracted_text) "
                    "VALUES(:v,:p,:t) "
                    "ON CONFLICT(document_version_id,page_no) DO NOTHING",
                    v=version["id"],
                    p=page,
                    t=text,
                )
            execute(
                c,
                "UPDATE document_versions SET parse_status='parsed' WHERE id=:id",
                id=version["id"],
            )
            execute(
                c,
                "UPDATE supplemental_documents SET status='ready',updated_at=now() WHERE "
                "id=:id AND "
                "status IN ('processing','ready')",
                id=doc["id"],
            )
    finally:
        gateway.close()
    return True


def build(job, model):
    """Try at most six downloads and two matched documents; never replay a 429."""
    from app.services.knowledge import persist_document

    results = list(discover(model))
    seen = set()
    published = 0
    for _ in range(6):
        if not results or published >= 2:
            break
        row = results.pop(0)
        url = row["url"]
        if url in seen:
            continue
        seen.add(url)
        with transaction() as c:
            fenced(c, job)
            blocked = one(
                c,
                "SELECT id FROM sources WHERE normalized_url=:u AND verification_status "
                "IN ('revoked','rejected')",
                u=url,
            )
            if blocked:
                continue
        progress(job, "researching_supplemental", 40)
        try:
            final, mime, data = fetch_public(url)
            if not publisher(final):
                continue
            if row.get("parentVersionId") and (final != url or mime != "application/pdf"):
                continue
            with transaction() as c:
                fenced(c, job)
                version = persist_document(
                    c, model, final, row["title"], mime, data, [], False, source_type="community"
                )
                ensure_document(
                    c,
                    model,
                    version,
                    row.get("publisherUrl") or final,
                    row.get("parentVersionId"),
                    url if row.get("parentVersionId") else None,
                )
            pages = pages_for(job, model, version, data, mime)
            if not matches(model, pages, row["title"]):
                continue
            if mime == "text/html":
                soup = BeautifulSoup(data, "html.parser")
                for tag in soup.select("a[href]"):
                    target = urldefrag(urljoin(final, tag["href"]))[0]
                    if (
                        publisher(target)
                        and re.search(r"\.pdf$", urlsplit(target).path, re.I)
                        and target not in seen
                    ):
                        results.insert(
                            0,
                            {
                                "url": target,
                                "title": row["title"],
                                "publisherUrl": final,
                                "parentVersionId": version["id"],
                            },
                        )
                        if sum(bool(r.get("publisherUrl")) for r in results) >= 2:
                            break
            if publish(job, model, version, pages, row.get("publisherUrl") or final):
                published += 1
        except DomainError as error:
            if error.code == "SOURCE_RATE_LIMITED":
                raise
            if error.status in {409, 429}:
                raise
            if error.code not in {
                "UPSTREAM_SEARCH_ERROR",
                "SOURCE_UNVERIFIED",
                "FILE_TYPE_NOT_ALLOWED",
                "FILE_TOO_LARGE",
            }:
                raise
    return published
