"""公共资料发布：可信来源、精确型号适用性和向量修订均由代码检查。"""

import re
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from app.adapters import ModelGateway, SearchGateway
from app.config import settings
from app.db import execute, json_value, many, one, transaction
from app.errors import DomainError
from app.fetch import fetch_public, public_addresses
from app.parser import parse_document
from app.security import normalize
from app.services.products import new_job
from app.services.queue import LeaseHeartbeat, fail, fenced, finish, progress
from app.services.source_discovery import allowed_url, discover_candidates, manual_links
from app.storage import Storage, immutable_key, sha256


def applicable(text, model):
    """拒绝近似/家族型号，并要求明确覆盖指定地区和硬件修订。"""
    normalized = normalize(text)
    for value in (model["model_code"], model["region"], model["hardware_revision"]):
        if value and not re.search(
            r"(?<![a-z0-9_-])" + re.escape(normalize(value)) + r"(?![a-z0-9_-])", normalized
        ):
            return False
    return True


def official_url(url, domains):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def register_official_source(connection, brand_name, url):
    """运营登记可信品牌的候选 URL；内容核验和发布仍由研究任务完成。"""
    brand = one(
        connection,
        "SELECT official_domains FROM brands WHERE normalized_name=:name",
        name=normalize(brand_name),
    )
    if not brand or not official_url(url, brand["official_domains"] or []):
        raise DomainError(422, "SOURCE_UNVERIFIED", "来源必须属于已登记的品牌官方域名")
    public_addresses(url)
    source = one(
        connection,
        "INSERT INTO sources(url,normalized_url,domain,source_type,"
        "is_official,verification_status) "
        "VALUES (:url,:url,:domain,'official',true,'pending') ON CONFLICT(normalized_url) "
        "DO UPDATE SET normalized_url=EXCLUDED.normalized_url RETURNING *",
        url=url,
        domain=urlsplit(url).hostname,
    )
    if source["verification_status"] in {"revoked", "rejected"}:
        raise DomainError(409, "SOURCE_UNVERIFIED", "已撤销或拒绝的来源不能重新登记")
    return source


def known_model_sources(connection, model):
    """复用已登记官方来源，补足搜索漏召回；路径须明确包含当前型号。

    登记来源仍可为 pending，不能直接成为检索证据；随后照常下载、核验和发布。
    匹配的是 URL 路径，不把 query 中的型号字符串当作型号适用证据。
    """
    domains = model["official_domains"] or []
    if not domains:
        return []
    rows = many(
        connection,
        "SELECT url FROM sources WHERE verification_status IN ('pending','verified') "
        "ORDER BY created_at LIMIT 100",
    )
    # 官网路径可能省略型号中的连字符；仍要求完整字母数字边界，正文核验不放宽。
    code = r"[-_]?".join(
        re.escape(part) for part in re.split(r"[-_]", normalize(model["model_code"]))
    )
    return [
        {"url": row["url"], "title": f"{model['brand_name']} {model['model_code']} 官方资料"}
        for row in rows
        if official_url(row["url"], domains)
        and re.search(
            r"(?<![a-z0-9])" + code + r"(?![a-z0-9])", normalize(unquote(urlsplit(row["url"]).path))
        )
    ]


def persist_document(connection, model, url, title, mime, data, pages, trusted, language="zh-CN"):
    """未核验内容仅落在 document_pages；禁止当作知识片段提前发布。"""
    existing = one(
        connection,
        (
            "SELECT v.* FROM document_versions v JOIN documents d ON "
            "d.id=v.document_id JOIN sources s ON s.id=d.source_id WHERE "
            "s.normalized_url=:url AND v.sha256=:hash"
        ),
        url=url,
        hash=sha256(data),
    )
    if existing:
        # 同 URL/内容哈希复用不可变版本，避免刷新留下未引用的公共对象。
        return existing
    storage = Storage()
    key = immutable_key("public")
    storage.put(key, data, mime)
    asset = one(
        connection,
        (
            "INSERT INTO "
            "assets(scope,object_key,sha256,mime_type,size_bytes,kind,status) "
            "VALUES "
            "('public',:key,:hash,:mime,:size,'official_document','available') "
            "RETURNING id"
        ),
        key=key,
        hash=sha256(data),
        mime=mime,
        size=len(data),
    )
    status = "verified" if trusted else "pending"
    source = one(
        connection,
        (
            "INSERT INTO "
            "sources(url,normalized_url,domain,source_type,is_official,verification_status,last_verified_at)"
            " VALUES (:url,:url,:domain,'official',:trusted,:status,CASE WHEN "
            ":trusted THEN now() ELSE NULL END) ON CONFLICT(normalized_url) DO "
            "UPDATE SET normalized_url=EXCLUDED.normalized_url RETURNING *"
        ),
        url=url,
        domain=urlsplit(url).hostname,
        trusted=trusted,
        status=status,
    )
    document = one(
        connection,
        (
            "INSERT INTO "
            "documents(source_id,logical_key,title,language,document_type) VALUES"
            " (:s,:key,:title,:language,'support') ON "
            "CONFLICT(source_id,logical_key) DO UPDATE SET title=EXCLUDED.title "
            "RETURNING *"
        ),
        s=source["id"],
        key=sha256(url.encode()),
        title=title[:1000],
        language=language,
    )
    version = one(
        connection,
        (
            "INSERT INTO "
            "document_versions(document_id,asset_id,sha256,mime_type,fetched_at,parse_status,verification_status,verified_at)"
            " VALUES (:d,:a,:hash,:mime,now(),'parsed',:status,CASE WHEN :trusted"
            " THEN now() ELSE NULL END) ON CONFLICT(document_id,sha256) DO UPDATE"
            " SET verification_status=CASE WHEN "
            "document_versions.verification_status IN ('revoked','rejected') THEN"
            " document_versions.verification_status ELSE "
            "EXCLUDED.verification_status END, verified_at=EXCLUDED.verified_at "
            "RETURNING *"
        ),
        d=document["id"],
        a=asset["id"],
        hash=sha256(data),
        mime=mime,
        status=status,
        trusted=trusted,
    )
    for page, text in pages:
        execute(
            connection,
            (
                "INSERT INTO "
                "document_pages(document_version_id,page_no,extracted_text) VALUES "
                "(:v,:page,:text) ON CONFLICT(document_version_id,page_no) DO NOTHING"
            ),
            v=version["id"],
            page=page,
            text=text,
        )
    if trusted:
        execute(
            connection,
            (
                "INSERT INTO "
                "document_model_links(document_version_id,product_model_id,match_type,confidence)"
                " VALUES (:v,:m,'exact',1) ON CONFLICT DO NOTHING"
            ),
            v=version["id"],
            m=model["id"],
        )
        execute(
            connection,
            (
                "UPDATE product_models SET "
                "identity_status='verified',verification_status='verified',verified_at=now()"
                " WHERE id=:id"
            ),
            id=model["id"],
        )
    return version


def document_chunks(pages):
    """页码与分片编号保持稳定；重试不能用当前批次的位置覆盖旧片段。"""
    pieces = [
        (page, text[offset : offset + 2000])
        for page, text in pages
        for offset in range(0, len(text), 2000)
        if text[offset : offset + 2000].strip()
    ]
    return [(number, page, text) for number, (page, text) in enumerate(pieces, 1)]


def require_public_document(connection, model, version):
    """付费前与发布事务内检查三级核验；来源撤销不能被续传或缓存绕过。"""
    valid = one(
        connection,
        (
            "SELECT m.id FROM product_models m JOIN document_model_links l ON "
            "l.product_model_id=m.id JOIN document_versions v ON "
            "v.id=l.document_version_id JOIN documents d ON d.id=v.document_id "
            "JOIN sources s ON s.id=d.source_id JOIN assets a ON a.id=v.asset_id "
            "WHERE m.id=:m AND v.id=:v AND m.identity_status='verified' AND "
            "m.verification_status='verified' AND "
            "v.verification_status='verified' AND "
            "s.verification_status='verified' AND a.scope='public' AND "
            "l.match_type='exact' FOR SHARE OF m,v,s"
        ),
        m=model["id"],
        v=version["id"],
    )
    if not valid:
        raise DomainError(409, "SOURCE_UNVERIFIED", "证据尚未通过公共发布核验")


def publish_chunks(connection, model, version, pages, vectors):
    """完整发布入口保留给受控导入；Worker 使用相同核验边界分批提交。"""
    publish_chunk_batch(connection, model, version, document_chunks(pages), vectors)


def publish_chunk_batch(connection, model, version, chunks, vectors):
    require_public_document(connection, model, version)
    if len(chunks) != len(vectors):
        raise ValueError("Embedding count mismatch")
    for (index, page, text), vector in zip(chunks, vectors, strict=True):
        execute(
            connection,
            (
                "INSERT INTO "
                "knowledge_chunks(document_version_id,product_model_id,chunk_no,page_start,page_end,content,"
                " "
                "content_type,verification_status,embedding,embedding_model,embedding_revision)"
                " VALUES "
                "(:v,:m,:n,:page,:page,:content,'text','verified',CAST(:vector AS "
                "vector),:model,:revision) ON "
                "CONFLICT(document_version_id,product_model_id,chunk_no) DO UPDATE "
                "SET "
                "embedding=EXCLUDED.embedding,embedding_model=EXCLUDED.embedding_model,embedding_revision=EXCLUDED.embedding_revision"
            ),
            v=version["id"],
            m=model["id"],
            n=index,
            page=page,
            content=text,
            vector=json_value(vector),
            model=settings().embedding_model,
            revision=settings().embedding_revision,
        )


def research(job):
    """研究、隔离解析、向量化分阶段推进，同一公共构建不重复请求付费服务。"""
    model_id = job["payload"]["productModelId"]
    progress(job, "researching", 10)
    with transaction() as connection:
        model = one(
            connection,
            (
                "SELECT m.*,b.name AS brand_name,b.official_domains FROM "
                "product_models m JOIN brands b ON b.id=m.brand_id WHERE m.id=:id"
            ),
            id=model_id,
        )
    mock = settings().model_provider == "mock"
    if mock:
        results = [
            {"url": "https://example.invalid/itemtalk-demo", "title": "演示设备官方资料（测试）"}
        ]
    else:
        with transaction() as connection:
            known = known_model_sources(connection, model)
        search = SearchGateway()
        try:
            results = discover_candidates(model, known, search.search)
        finally:
            search.client.close()
    published = 0
    problems = set()
    seen = set()
    initial_count = len(results)
    queued = {row["url"] for row in results}
    for index in range(settings().search_max_results + 5):
        if index >= len(results):
            break
        result = results[index]
        domains = model["official_domains"] or []
        if result["url"] in seen:
            continue
        seen.add(result["url"])
        if not mock and not allowed_url(result["url"], domains):
            continue
        progress(job, "parsing", 35)
        try:
            if mock:
                url, mime = result["url"], "text/plain"
                identity = f"{model['model_code']} {model['region'] or ''} "
                identity += model["hardware_revision"] or ""
                data = (identity + "\n长按电源键 3 秒开机。请勿带电拆机。").encode()
            else:
                url, mime, data = fetch_public(result["url"])
                if not allowed_url(url, domains):
                    problems.add("下载跳转到尚未核验的域名")
                    continue
                if mime == "text/html" and index < initial_count:
                    # Expand only the initial result level; never recursively crawl PDF links.
                    results.extend(
                        link
                        for link in manual_links(url, data, domains)
                        if link["url"] not in queued
                    )
                    queued.update(row["url"] for row in results)
            with transaction() as connection:
                fenced(connection, job)
                version = persist_document(
                    connection, model, url, result["title"], mime, data, [], False
                )
                execute(
                    connection,
                    "UPDATE document_versions SET parse_status='pending' WHERE id=:id",
                    id=version["id"],
                )
            payload = {
                "productModelId": str(model_id),
                "documentVersionId": str(version["id"]),
                "buildJobId": str(job["id"]),
                "buildAttempt": job["attempts"],
            }
            trusted = execute_subtask(job, "parse", payload, parse_task)
            if not trusted:
                problems.add("资料与型号、地区或版本尚未匹配")
                continue
            progress(job, "embedding", 65)
            execute_subtask(job, "embed", payload, embed_task)
            published += 1
        except DomainError as error:
            if error.code not in {
                "UPSTREAM_SEARCH_ERROR",
                "SOURCE_UNVERIFIED",
                "FILE_TYPE_NOT_ALLOWED",
                "FILE_TOO_LARGE",
            }:
                raise
            problems.add(
                "下载或解析失败"
                if error.code == "UPSTREAM_SEARCH_ERROR"
                else "资料格式、内容或适用性未通过核验"
            )
    if not model["official_domains"] and not mock:
        problems.add("品牌官方来源尚未核验")
    if not results:
        problems.add("暂未发现可安全读取的官方资料链接")
    summary = "；".join(sorted(problems)) or "没有找到满足型号、地区和官方来源要求的资料"
    with transaction() as connection:
        finish(
            connection,
            job,
            "succeeded" if published else "partial_success",
            None if published else summary + "。仍可提问并获得低风险通用帮助。",
        )
        if published:
            from app.services.manuals import schedule

            schedule(connection, model_id)


def execute_subtask(parent, job_type, payload, handler):
    """并发 1 的 Worker 内顺序执行子任务，同时保留独立租约和任务审计记录。"""
    with transaction() as connection:
        fenced(connection, parent)
        child = new_job(connection, job_type, payload)
        child = one(
            connection,
            (
                "UPDATE jobs SET status='running',attempts=1,started_at=now(), "
                "lease_token=:token,lease_until=now()+make_interval(secs=>:seconds) "
                "WHERE id=:id RETURNING *"
            ),
            token=uuid4(),
            seconds=settings().lease_seconds,
            id=child["id"],
        )
    try:
        with LeaseHeartbeat(child):
            return handler(child)
    except Exception as error:
        if isinstance(error, DomainError) and error.code in {
            "SOURCE_UNVERIFIED",
            "FILE_TYPE_NOT_ALLOWED",
            "FILE_TOO_LARGE",
        }:
            # The parent moves on to another document; this immutable failed input
            # must not leave a queued child whose parent has already completed.
            with transaction() as connection:
                fenced(connection, child)
                execute(
                    connection, "UPDATE jobs SET max_attempts=attempts WHERE id=:id", id=child["id"]
                )
        fail(child, error.message if isinstance(error, DomainError) else "资料子任务失败")
        raise


def parent_fenced(connection, job):
    """子任务还需验证所属构建尝试；旧父任务恢复后，旧子任务不得发布结果。"""
    fenced(connection, job)
    parent = one(
        connection,
        (
            "SELECT id FROM jobs WHERE id=:id AND attempts=:attempt AND "
            "status='running' AND lease_until>now() FOR UPDATE"
        ),
        id=job["payload"]["buildJobId"],
        attempt=job["payload"]["buildAttempt"],
    )
    if not parent:
        raise DomainError(409, "RESOURCE_CONFLICT", "所属资料构建租约已失效")


def task_document(connection, job):
    parent_fenced(connection, job)
    return one(
        connection,
        (
            "SELECT v.*,a.object_key,s.url,s.id AS source_id,b.official_domains, "
            "m.model_code,m.region,m.hardware_revision FROM document_versions v "
            "JOIN assets a ON a.id=v.asset_id JOIN documents d ON "
            "d.id=v.document_id JOIN sources s ON s.id=d.source_id JOIN "
            "product_models m ON m.id=:model JOIN brands b ON b.id=m.brand_id "
            "WHERE v.id=:version AND a.scope='public'"
        ),
        model=job["payload"]["productModelId"],
        version=job["payload"]["documentVersionId"],
    )


def parse_task(job):
    """解析结果先暂存在页表，精确适用性核验通过后才标记可发布。"""
    with transaction() as connection:
        document = task_document(connection, job)
    if not document:
        raise DomainError(409, "SOURCE_UNVERIFIED", "公共资料版本不存在")
    data = Storage().read(document["object_key"])
    pages = parse_document(data, document["mime_type"])
    trusted = (
        settings().model_provider == "mock"
        or official_url(document["url"], document["official_domains"] or [])
    ) and applicable("\n".join(text for _, text in pages), document)
    with transaction() as connection:
        parent_fenced(connection, job)
        current = task_document(connection, job)
        source = one(
            connection,
            ("SELECT verification_status FROM sources WHERE id=:id FOR UPDATE"),
            id=document["source_id"],
        )
        # 来源撤销是运营决定，自动研究不能静默恢复已撤销证据。
        trusted = trusted and source["verification_status"] not in {"revoked", "rejected"}
        trusted = trusted and current["verification_status"] not in {"revoked", "rejected"}
        for page, text in pages:
            execute(
                connection,
                (
                    "INSERT INTO "
                    "document_pages(document_version_id,page_no,extracted_text) VALUES "
                    "(:v,:p,:text) ON CONFLICT(document_version_id,page_no) DO NOTHING"
                ),
                v=document["id"],
                p=page,
                text=text,
            )
        execute(
            connection,
            ("UPDATE document_versions SET parse_status='parsed' WHERE id=:id"),
            id=document["id"],
        )
        if trusted:
            model_id = job["payload"]["productModelId"]
            execute(
                connection,
                (
                    "UPDATE sources SET "
                    "is_official=true,verification_status='verified',last_verified_at=now()"
                    " WHERE id=:id"
                ),
                id=document["source_id"],
            )
            execute(
                connection,
                (
                    "UPDATE document_versions SET "
                    "verification_status='verified',verified_at=now() WHERE id=:id"
                ),
                id=document["id"],
            )
            execute(
                connection,
                (
                    "UPDATE product_models SET "
                    "identity_status='verified',verification_status='verified',verified_at=now()"
                    " WHERE id=:id"
                ),
                id=model_id,
            )
            execute(
                connection,
                (
                    "INSERT INTO "
                    "document_model_links(document_version_id,product_model_id,match_type,confidence)"
                    " VALUES (:v,:m,'exact',1) ON CONFLICT DO NOTHING"
                ),
                v=document["id"],
                m=model_id,
            )
        finish(connection, job, "succeeded" if trusted else "partial_success")
    return trusted


def embed_task(job):
    """每批最多十片，保留完成批次；长手册与失败重试不重复支付已核验向量。"""
    with transaction() as connection:
        document = task_document(connection, job)
        pages = [
            (row["page_no"], row["extracted_text"])
            for row in many(
                connection,
                ("SELECT * FROM document_pages WHERE document_version_id=:id ORDER BY page_no"),
                id=document["id"],
            )
        ]
        existing = {
            row["chunk_no"]: row
            for row in many(
                connection,
                "SELECT chunk_no,page_start,page_end,content,verification_status,"
                "embedding IS NOT NULL AS has_embedding,embedding_model,embedding_revision "
                "FROM knowledge_chunks WHERE document_version_id=:version "
                "AND product_model_id=:model",
                version=document["id"],
                model=job["payload"]["productModelId"],
            )
        }
    chunks = document_chunks(pages)
    config = settings()
    pending = []
    for number, page, text in chunks:
        cached = existing.get(number)
        if cached and (
            cached["page_start"] != page
            or cached["page_end"] != page
            or cached["content"] != text
            or cached["verification_status"] != "verified"
        ):
            raise DomainError(409, "SOURCE_UNVERIFIED", "已发布分片与不可变资料不一致或已撤销")
        if not cached or not (
            cached["has_embedding"]
            and cached["embedding_model"] == config.embedding_model
            and cached["embedding_revision"] == config.embedding_revision
        ):
            pending.append((number, page, text))
    if set(existing) - {number for number, _, _ in chunks}:
        raise DomainError(409, "SOURCE_UNVERIFIED", "已发布分片不属于当前不可变资料")
    model = {"id": job["payload"]["productModelId"]}
    gateway = ModelGateway()
    try:
        for offset in range(0, len(pending), 10):
            batch = pending[offset : offset + 10]
            with transaction() as connection:
                parent_fenced(connection, job)
                require_public_document(connection, model, document)
            # 仍由适配器逐批扣除正常每日额度，未增加预算；额度不足保留已完成批次。
            vectors = gateway.embed([text for _, _, text in batch], f"build:{model['id']}")
            with transaction() as connection:
                parent_fenced(connection, job)
                publish_chunk_batch(connection, model, document, batch, vectors)
            progress(job, "embedding", int(100 * (offset + len(batch)) / len(pending)))
    finally:
        gateway.close()
    with transaction() as connection:
        parent_fenced(connection, job)
        require_public_document(connection, model, document)
        finish(connection, job)
