"""ItemTalk HTTP 入口：认证与传输层，业务规则统一委托给领域服务。"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import IntegrityError

from app import operations
from app.config import settings
from app.db import actor, execute, json_value, many, one, transaction, validate_runtime
from app.errors import DomainError, missing
from app.schemas import (
    ConfirmRequest,
    ConversationRequest,
    Credentials,
    FeedbackRequest,
    GuidanceFeedback,
    GuidanceRequest,
    GuidanceResume,
    IdentifyRequest,
    InstanceRequest,
    KnowledgeRequest,
    ManualJobRequest,
    MessageRequest,
    PresignRequest,
    SectionJobRequest,
)
from app.security import (
    consume,
    decode_token,
    idempotent,
    normalize,
    password_hasher,
    remember,
    require_write_headers,
    token_for,
    verify_password,
)
from app.services import chat, feedback, guidance, lifecycle, manuals, memories, products, uploads
from app.services.transcription import transcribe_asset
from app.speech import asr_enabled
from app.storage import Storage


@asynccontextmanager
async def lifespan(_app):
    if settings().environment in {"production", "staging"}:
        validate_runtime("app")
    yield


app = FastAPI(title="ItemTalk API", version="0.1.0", lifespan=lifespan)
logger = logging.getLogger(__name__)
HEARTBEAT_SQL = "UPDATE assistant_runs SET heartbeat_at=now() WHERE id=:id AND status='running'"


@app.exception_handler(DomainError)
async def domain_error(_request, error):
    _request.scope["v2_error_code"] = error.code
    headers = {"Retry-After": "60"} if error.status == 429 else None
    return JSONResponse(error.body(), status_code=error.status, headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_request, _error):
    # Pydantic 的 input 可能含密码，禁止直接返回或记录其异常详情。
    error = DomainError(422, "VALIDATION_ERROR", "输入格式无效，请检查字段内容")
    _request.scope["v2_error_code"] = error.code
    return JSONResponse(error.body(), status_code=422)


@app.exception_handler(IntegrityError)
async def integrity_error(_request, _error):
    error = DomainError(409, "RESOURCE_CONFLICT", "资源状态冲突，请刷新后重试")
    _request.scope["v2_error_code"] = error.code
    return JSONResponse(error.body(), status_code=409)


@app.middleware("http")
async def guard_writes(request, call_next):
    try:
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.endswith(
            "/content"
        ):
            require_write_headers(request)
        if request.url.path.startswith("/api/v1/") and request.url.path != "/api/v1/health":
            client = request.client.host if request.client else "unknown"
            subject = f"http:{client}"
            cookie = request.cookies.get("itemtalk_auth")
            if cookie and request.url.path not in {"/api/v1/auth/login", "/api/v1/auth/register"}:
                subject = f"http-user:{decode_token(cookie)['sub']}"
            with transaction() as connection:
                consume(
                    connection,
                    subject,
                    datetime.now(UTC).strftime("%Y-%m-%dT%H:%M"),
                    settings().rate_limit_per_minute,
                )
        # 中间件上下文会复制到同步路由线程及 SSE 子任务；依赖线程里设置 ContextVar
        # 不能传播回路由，因此必须在进入路由之前绑定已验证的身份。
        cookie = request.cookies.get("itemtalk_auth")
        identity = None
        if cookie and request.url.path not in {"/api/v1/auth/login", "/api/v1/auth/register"}:
            identity = decode_token(cookie)["sub"]
        if request.url.path.endswith("/content"):
            purpose = "upload" if request.method == "PUT" else "read"
            claims = decode_token(request.query_params.get("token", ""), purpose)
            identity = claims.get("owner")
            if not identity:
                raise DomainError(401, "AUTH_REQUIRED", "上传或读取链接已失效，请重新获取")
        with actor(identity):
            return await call_next(request)
    except DomainError as error:
        request.scope["v2_error_code"] = error.code
        return JSONResponse(error.body(), status_code=error.status)
    except Exception as error:
        request.scope["v2_error_code"] = "INTERNAL_ERROR"
        # 捕获传输层未预期故障，避免框架默认堆栈包含上游响应或用户参数。
        logger.error("HTTP failure: error_type=%s", type(error).__name__)
        failure = DomainError(500, "INTERNAL_ERROR", "服务暂时不可用，请稍后重试")
        return JSONResponse(failure.body(), status_code=500)


def user(request: Request):
    claims = decode_token(request.cookies.get("itemtalk_auth", ""))
    with transaction() as connection:
        row = one(
            connection,
            ("SELECT * FROM users WHERE id=:id AND status='active' AND deleted_at IS NULL"),
            id=UUID(claims["sub"]),
        )
    if not row:
        raise DomainError(401, "AUTH_REQUIRED", "请重新登录")
    return row


def user_view(row):
    return {"id": str(row["id"]), "email": row["login"], "displayName": row["display_name"]}


def request_key(key):
    if not key or len(key) > 160:
        raise DomainError(422, "VALIDATION_ERROR", "需要有效的 Idempotency-Key")
    return key


@app.get("/api/v1/health")
def health():
    with transaction() as connection:
        execute(connection, "SELECT 1 FROM users LIMIT 1")
    return {"status": "healthy", "buildVersion": settings().build_version}


@app.get("/api/v1/capabilities")
def capabilities(current=Depends(user)):
    with transaction() as connection:
        admin = operations.is_admin(connection)
    return {"speechToText": asr_enabled(), "recordingMaxSeconds": 60, "adminConsole": admin}


@app.get("/api/v1/admin/overview")
def admin_overview(days: int = Query(default=1, ge=1, le=30), current=Depends(user)):
    with transaction() as connection:
        return operations.overview(connection, days)


@app.get("/api/v1/admin/metrics")
def admin_metrics(days: int = Query(default=1, ge=1, le=30), current=Depends(user)):
    with transaction() as connection:
        return {"groups": operations.metrics(connection, days)}


@app.get("/api/v1/admin/runtime-config")
def admin_runtime(current=Depends(user)):
    with transaction() as connection:
        operations.require_admin(connection)
        return operations.runtime(connection)


@app.patch("/api/v1/admin/runtime-config")
def admin_update(
    request: operations.RuntimeUpdate,
    current=Depends(user),
    key: str = Header(alias="Idempotency-Key"),
):
    with transaction() as connection:
        operations.require_admin(connection)
        body = request.model_dump()
        previous = idempotent(connection, current["id"], "admin/runtime-config", key, body)
        if previous:
            return previous["response_snapshot"]
        result = operations.update_runtime(connection, request)
        remember(
            connection, current["id"], "admin/runtime-config", key, body, current["id"], result, 200
        )
        return result


@app.get("/api/v1/admin/audit")
def admin_audit(before: int | None = Query(default=None, ge=1), current=Depends(user)):
    from app.db import many

    with transaction() as connection:
        operations.require_admin(connection)
        rows = many(
            connection,
            "SELECT id,actor_id,version,before_value,after_value,created_at "
            "FROM runtime_config_audit WHERE (CAST(:b AS bigint) IS NULL OR id<:b) "
            "ORDER BY id DESC LIMIT 50",
            b=before,
        )
        return {"items": rows, "nextBefore": rows[-1]["id"] if len(rows) == 50 else None}


@app.post("/api/v1/assets/{asset_id}/transcribe")
def transcribe(asset_id: UUID, current=Depends(user), key: str = Header(alias="Idempotency-Key")):
    operation = f"assets/{asset_id}/transcribe"
    with transaction() as connection:
        products.owned(connection, "assets", asset_id, current["id"])
        previous = idempotent(connection, current["id"], operation, key, {})
        if previous:
            return previous["response_snapshot"]
        result = transcribe_asset(connection, asset_id, current["id"])
        remember(connection, current["id"], operation, key, {}, asset_id, result, 200)
        return result


@app.post("/api/v1/auth/register", status_code=201)
def register(body: Credentials):
    with transaction() as connection:
        row = one(
            connection,
            ("SELECT * FROM itemtalk_register(:email,:normalized,:password)"),
            email=body.email,
            normalized=normalize(body.email),
            password=password_hasher.hash(body.password),
        )
        if not row:
            raise DomainError(400, "VALIDATION_ERROR", "该邮箱不能用于注册")
    return user_view(row)


@app.post("/api/v1/auth/login", status_code=204)
def login(body: Credentials, response: Response):
    with transaction() as connection:
        row = one(
            connection,
            ("SELECT * FROM itemtalk_login(:email)"),
            email=normalize(body.email),
        )
    if not row or not verify_password(row["hashed_password"], body.password):
        raise DomainError(400, "VALIDATION_ERROR", "邮箱或密码不正确")
    config = settings()
    response.set_cookie(
        "itemtalk_auth",
        token_for(row["id"]),
        max_age=config.auth_ttl_seconds,
        httponly=True,
        secure=config.auth_cookie_secure,
        samesite="lax",
        path=config.auth_cookie_path,
    )


@app.post("/api/v1/auth/logout", status_code=204)
def logout(response: Response, current=Depends(user)):
    response.delete_cookie(
        "itemtalk_auth",
        path=settings().auth_cookie_path,
        secure=settings().auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@app.get("/api/v1/me")
def me(current=Depends(user)):
    return user_view(current)


@app.delete("/api/v1/me", status_code=202)
def delete_account(response: Response, current=Depends(user)):
    with transaction(exclusive=True) as connection:
        result = lifecycle.account(connection, current["id"])
    response.delete_cookie(
        "itemtalk_auth",
        path=settings().auth_cookie_path,
        secure=settings().auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return result


@app.get("/api/v1/me/memories")
def list_memories(current=Depends(user), instanceId: UUID | None = None):
    with transaction() as connection:
        return memories.list_for(connection, current["id"], instanceId)


@app.post("/api/v1/me/memories/{memory_id}/confirm")
def confirm_memory(memory_id: UUID, current=Depends(user)):
    with transaction() as connection:
        return memories.confirm(connection, memory_id, current["id"])


@app.delete("/api/v1/me/memories/{memory_id}", status_code=204)
def delete_memory(memory_id: UUID, current=Depends(user)):
    with transaction() as connection:
        memories.delete(connection, memory_id, current["id"])


@app.post("/api/v1/assets/presign", status_code=201)
def presign(
    body: PresignRequest, current=Depends(user), key: str = Header(alias="Idempotency-Key")
):
    operation = "assets/presign"
    with transaction() as connection:
        old = idempotent(connection, current["id"], operation, key, body.model_dump())
        asset = (
            products.owned(connection, "assets", old["resource_id"], current["id"])
            if old
            else uploads.create_upload(connection, body, current["id"])
        )
        if not old:
            remember(
                connection, current["id"], operation, key, body.model_dump(), asset["id"], {}, 201
            )
        return uploads.presign_view(asset)


@app.put("/api/v1/assets/{asset_id}/content", status_code=204)
async def local_upload(asset_id: UUID, request: Request, token: str):
    """仅开发存储使用；签名绑定资产和用途，不暴露本地路径。"""
    if settings().storage_backend != "local":
        missing()
    claims = decode_token(token, "upload")
    if claims["sub"] != str(asset_id):
        missing()
    with transaction() as connection:
        asset = one(connection, "SELECT * FROM assets WHERE id=:id FOR UPDATE", id=asset_id)
        if not asset or asset["status"] != "pending" or asset["expires_at"] < datetime.now(UTC):
            raise DomainError(409, "RESOURCE_CONFLICT", "上传已结束或过期")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > min(asset["size_bytes"], 25 * 1024 * 1024):
                raise DomainError(413, "FILE_TOO_LARGE", "文件超过大小限制")
        store = Storage()
        # 临时对象可覆盖以支持网络重试；确认后的对象使用另一个不可变键。
        store.delete(asset["object_key"])
        store.put(asset["object_key"], bytes(data), asset["mime_type"])


@app.get("/api/v1/assets/{asset_id}/content")
def local_read(asset_id: UUID, token: str):
    if settings().storage_backend != "local":
        missing()
    claims = decode_token(token, "read")
    if claims["sub"] != str(asset_id):
        missing()
    with transaction() as connection:
        asset = one(
            connection,
            (
                "SELECT a.* FROM assets a JOIN users u ON u.id=a.owner_user_id WHERE "
                "a.id=:id AND a.status='available' AND a.deleted_at IS NULL AND "
                "u.status='active' AND u.deleted_at IS NULL"
            ),
            id=asset_id,
        )
        if not asset:
            missing()
    return Response(
        Storage().read(asset["object_key"]),
        media_type=asset["mime_type"],
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/v1/assets/{asset_id}/confirm")
def confirm_asset(asset_id: UUID, current=Depends(user)):
    with transaction() as connection:
        return uploads.confirm_upload(connection, asset_id, current["id"])


@app.get("/api/v1/assets/{asset_id}")
def get_asset(asset_id: UUID, current=Depends(user)):
    with transaction() as connection:
        return uploads.asset_view(products.owned(connection, "assets", asset_id, current["id"]))


@app.delete("/api/v1/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: UUID, current=Depends(user)):
    with transaction(exclusive=True) as connection:
        lifecycle.resource(connection, current["id"], "asset", asset_id)


@app.post("/api/v1/products/identify", status_code=202)
def identify(
    body: IdentifyRequest, current=Depends(user), key: str = Header(alias="Idempotency-Key")
):
    with transaction() as connection:
        old = idempotent(connection, current["id"], "products/identify", key, body.model_dump())
        if old:
            return products.identification_view(
                connection,
                products.owned(connection, "identifications", old["resource_id"], current["id"]),
            )
        result = products.identify(connection, body, current["id"], key)
        remember(
            connection,
            current["id"],
            "products/identify",
            key,
            body.model_dump(),
            UUID(result["id"]),
            result,
            202,
        )
        return result


@app.get("/api/v1/identifications/{attempt_id}")
def get_identification(attempt_id: UUID, current=Depends(user)):
    with transaction() as connection:
        return products.identification_view(
            connection, products.owned(connection, "identifications", attempt_id, current["id"])
        )


@app.post("/api/v1/identifications/{attempt_id}/confirm")
def confirm_identification(
    attempt_id: UUID,
    body: ConfirmRequest,
    current=Depends(user),
    key: str = Header(alias="Idempotency-Key"),
):
    operation = f"identifications/{attempt_id}/confirm"
    with transaction() as connection:
        products.owned(connection, "identifications", attempt_id, current["id"])
        old = idempotent(connection, current["id"], operation, key, body.model_dump())
        if old:
            return old["response_snapshot"]
        result = products.confirm(connection, attempt_id, body, current["id"])
        remember(
            connection,
            current["id"],
            operation,
            key,
            body.model_dump(),
            UUID(result["id"]),
            result,
            200,
        )
        return result


@app.get("/api/v1/me/products")
def list_products(current=Depends(user)):
    with transaction() as connection:
        return {"items": products.instances(connection, current["id"])}


@app.get("/api/v1/me/products/{instance_id}")
def get_product(instance_id: UUID, current=Depends(user)):
    with transaction() as connection:
        return products.instance_view(
            connection, products.owned(connection, "instances", instance_id, current["id"])
        )


@app.delete("/api/v1/me/products/{instance_id}", status_code=204)
def delete_instance(instance_id: UUID, current=Depends(user)):
    with transaction(exclusive=True) as connection:
        lifecycle.resource(connection, current["id"], "instance", instance_id)


@app.post("/api/v1/me/products", status_code=201)
def create_product(
    body: InstanceRequest, current=Depends(user), key: str = Header(alias="Idempotency-Key")
):
    with transaction() as connection:
        old = idempotent(connection, current["id"], "me/products", key, body.model_dump())
        if old:
            return old["response_snapshot"]
        model = one(
            connection,
            (
                "SELECT * FROM product_models m WHERE m.id=:id AND "
                "(m.identity_status='verified' OR EXISTS(SELECT 1 FROM "
                "identification_attempts a WHERE a.confirmed_product_model_id=m.id "
                "AND a.user_id=:u AND a.status='confirmed' AND a.deleted_at IS NULL))"
            ),
            id=body.productModelId,
            u=current["id"],
        )
        if not model:
            missing()
        instance = products.create_instance(
            connection, body.productModelId, current["id"], body.nickname
        )
        result = products.instance_view(connection, instance)
        remember(
            connection,
            current["id"],
            "me/products",
            key,
            body.model_dump(),
            instance["id"],
            result,
            201,
        )
        return result


@app.post("/api/v1/products/{model_id}/knowledge-jobs", status_code=202)
def build_knowledge(
    model_id: UUID,
    body: KnowledgeRequest | None = None,
    current=Depends(user),
    key: str = Header(alias="Idempotency-Key"),
    retry: UUID | None = Header(default=None, alias="Retry-Of"),
):
    body = body or KnowledgeRequest()
    operation = f"products/{model_id}/knowledge-jobs"
    request_body = {**body.model_dump(), "retry": str(retry) if retry else None}
    with transaction() as connection:
        instance = one(
            connection,
            (
                "SELECT * FROM user_product_instances WHERE user_id=:u AND "
                "product_model_id=:m AND deleted_at IS NULL LIMIT 1"
            ),
            u=current["id"],
            m=model_id,
        )
        if not instance and not current["is_admin"]:
            missing()
        if body.forceRefresh and not current["is_admin"]:
            raise DomainError(403, "FORBIDDEN", "只有管理员可以刷新公共资料")
        old = idempotent(connection, current["id"], operation, key, request_body)
        if old:
            return products.job_view(
                products.accessible_job(
                    connection, old["resource_id"], current["id"], current["is_admin"]
                )
            )
        model = one(connection, "SELECT * FROM product_models WHERE id=:id", id=model_id)
        if not model:
            missing()
        if model["identity_status"] != "verified":
            raise DomainError(409, "PRODUCT_MODEL_NOT_VERIFIED", "型号尚未通过来源核验")
        if retry:
            products.accessible_job(connection, retry, current["id"], current["is_admin"])
        job = products.build_for(
            connection,
            model_id,
            instance["id"] if instance else None,
            current["id"],
            body.forceRefresh,
            retry,
        )
        remember(connection, current["id"], operation, key, request_body, job["id"], {}, 202)
        return products.job_view(job)


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: UUID, current=Depends(user)):
    with transaction() as connection:
        return products.job_view(
            products.accessible_job(connection, job_id, current["id"], current["is_admin"])
        )


@app.post("/api/v1/conversations", status_code=201)
def create_conversation(body: ConversationRequest, current=Depends(user)):
    with transaction() as connection:
        products.owned(connection, "instances", body.userProductInstanceId, current["id"])
        row = one(
            connection,
            (
                "INSERT INTO "
                "conversations(user_id,user_product_instance_id,title,status) VALUES "
                "(:u,:i,:title,'active') RETURNING *"
            ),
            u=current["id"],
            i=body.userProductInstanceId,
            title=body.title,
        )
        return chat.conversation_view(row)


@app.post("/api/v1/guidance-sessions", status_code=201)
def create_guidance(
    body: GuidanceRequest, current=Depends(user), key: str = Header(alias="Idempotency-Key")
):
    with transaction() as connection:
        return guidance.create(connection, current["id"], body, key)


@app.get("/api/v1/guidance-sessions")
def list_guidance(current=Depends(user), instanceId: UUID | None = None):
    with transaction() as connection:
        if instanceId:
            products.owned(connection, "instances", instanceId, current["id"])
        rows = many(
            connection,
            (
                "SELECT g.* FROM guidance_sessions g JOIN "
                "user_product_instances i ON i.id=g.user_product_instance_id "
                "WHERE g.user_id=:u AND g.deleted_at IS NULL AND "
                "i.deleted_at IS NULL AND (CAST(:i AS uuid) IS NULL OR "
                "g.user_product_instance_id=:i) ORDER BY g.updated_at DESC "
                "LIMIT 30"
            ),
            u=current["id"],
            i=instanceId,
        )
        return {"items": [guidance.view(connection, row) for row in rows]}


@app.get("/api/v1/guidance-sessions/{session_id}")
def get_guidance(session_id: UUID, current=Depends(user)):
    with transaction() as connection:
        return guidance.view(
            connection, guidance.selected_session(connection, session_id, current["id"])
        )


@app.post("/api/v1/guidance-sessions/{session_id}/feedback")
def guidance_feedback(
    session_id: UUID,
    body: GuidanceFeedback,
    current=Depends(user),
    key: str = Header(alias="Idempotency-Key"),
):
    with transaction() as connection:
        return guidance.feedback(connection, session_id, current["id"], body, key)


@app.post("/api/v1/guidance-sessions/{session_id}/resume")
def resume_guidance(
    session_id: UUID,
    body: GuidanceResume,
    current=Depends(user),
    key: str = Header(alias="Idempotency-Key"),
):
    with transaction() as connection:
        return guidance.resume(connection, session_id, current["id"], body, key)


@app.get("/api/v1/conversations")
def list_conversations(
    current=Depends(user),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: UUID | None = None,
    instanceId: UUID | None = None,
    q: str | None = Query(default=None, max_length=200),
):
    with transaction() as connection:
        if instanceId:
            products.owned(connection, "instances", instanceId, current["id"])
        position = (
            products.owned(connection, "conversations", cursor, current["id"]) if cursor else None
        )
        rows = many(
            connection,
            "SELECT c.* FROM conversations c JOIN user_product_instances i ON i"
            ".id=c.user_product_instance_id "
            "WHERE c.user_id=:u AND c.deleted_at IS NULL AND i.deleted_at IS NULL "
            "AND (CAST(:instance AS uuid) IS NULL OR c.user_product_instance_id"
            "=:instance) "
            "AND (CAST(:date AS timestamptz) IS NULL OR (c.updated_at,c.id)<(:d"
            "ate,:cursor)) "
            "AND (:search='' OR strpos(lower(COALESCE(c.title,'')),lower(:search))>0 "
            "OR EXISTS(SELECT 1 FROM messages m WHERE m.conversation_id=c.id "
            "AND strpos(lower(COALESCE(m.content,'')),lower(:search))>0)) "
            "ORDER BY c.updated_at DESC,c.id DESC LIMIT :count",
            u=current["id"],
            instance=instanceId,
            date=position["updated_at"] if position else None,
            cursor=cursor,
            search=(q or "").strip(),
            count=limit + 1,
        )
        return {
            "items": [chat.conversation_view(row) for row in rows[:limit]],
            "nextCursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


@app.delete("/api/v1/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, current=Depends(user)):
    with transaction(exclusive=True) as connection:
        lifecycle.resource(connection, current["id"], "conversation", conversation_id)


@app.post("/api/v1/messages/{message_id}/feedback", status_code=204)
def submit_message_feedback(message_id: UUID, body: FeedbackRequest, current=Depends(user)):
    with transaction() as connection:
        feedback.submit(connection, message_id, current["id"], body)


@app.get("/api/v1/products/{model_id}/manual")
def get_manual(
    model_id: UUID, response: Response, current=Depends(user), instanceId: UUID | None = None
):
    # 同一型号URL可带个人实例与确认状态，不能由共享代理或浏览器缓存复用。
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    with transaction() as connection:
        return manuals.get_manual(connection, model_id, current["id"], instanceId)


@app.get("/api/v1/manuals/{manual_id}/sections/{section_id}")
def get_manual_section(
    manual_id: UUID, section_id: UUID, response: Response, current=Depends(user)
):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie"
    with transaction() as connection:
        manuals.authorized(connection, manual_id, current["id"])
        section = one(
            connection,
            "SELECT * FROM manual_sections WHERE id=:id AND manual_id=:m",
            id=section_id,
            m=manual_id,
        )
        if not section:
            missing()
        return manuals.section_view(connection, section)


@app.post("/api/v1/products/{model_id}/manual-jobs", status_code=202)
def create_manual_job(
    model_id: UUID,
    body: ManualJobRequest | None = None,
    current=Depends(user),
    key: str = Header(alias="Idempotency-Key"),
    retry: UUID | None = Header(default=None, alias="Retry-Of"),
):
    body = body or ManualJobRequest()
    with transaction() as connection:
        instance = one(
            connection,
            (
                "SELECT * FROM user_product_instances WHERE user_id=:u AND product_"
                "model_id=:m AND deleted_at IS NULL LIMIT 1"
            ),
            u=current["id"],
            m=model_id,
        )
        if not instance and not current["is_admin"]:
            missing()
        if body.userProductInstanceId:
            instance = products.owned(
                connection, "instances", body.userProductInstanceId, current["id"]
            )
            if instance["product_model_id"] != model_id:
                missing()
        operation = f"products/{model_id}/manual-jobs"
        request_body = {**body.model_dump(mode="json"), "retry": str(retry) if retry else None}
        previous = idempotent(connection, current["id"], operation, key, request_body)
        if previous:
            return previous["response_snapshot"]
        manual = manuals.schedule(
            connection, model_id, retry, instance_id=body.userProductInstanceId, user=current["id"]
        )
        if not manual:
            raise DomainError(422, "EVIDENCE_INSUFFICIENT", "资料核验完成后才能生成说明书")
        if instance:
            execute(
                connection,
                (
                    "INSERT INTO job_request_links(job_id,user_id,user_product_instance"
                    "_id) VALUES (:j,:u,:i) ON CONFLICT DO NOTHING"
                ),
                j=manual["job_id"],
                u=current["id"],
                i=instance["id"],
            )
        result = products.job_view(
            one(connection, "SELECT * FROM jobs WHERE id=:id", id=manual["job_id"])
        )
        remember(
            connection, current["id"], operation, key, request_body, manual["job_id"], result, 202
        )
        return result


@app.post("/api/v1/manuals/{manual_id}/section-jobs", status_code=202)
def create_section_job(
    manual_id: UUID,
    body: SectionJobRequest,
    current=Depends(user),
    key: str = Header(alias="Idempotency-Key"),
):
    with transaction() as connection:
        manual = manuals.authorized(connection, manual_id, current["id"])
        if manual["visibility"] == "public":
            owned_instance = one(
                connection,
                (
                    "SELECT id FROM user_product_instances WHERE user_id=:u AND "
                    "product_model_id=:m AND deleted_at IS NULL LIMIT 1"
                ),
                u=current["id"],
                m=manual["product_model_id"],
            )
            if not owned_instance and not current["is_admin"]:
                missing()
        operation = f"manuals/{manual_id}/section-jobs"
        request_body = body.model_dump(mode="json")
        previous = idempotent(connection, current["id"], operation, key, request_body)
        if previous:
            return previous["response_snapshot"]
        job = manuals.enqueue_sections(connection, manual_id, current["id"], body.sectionIds)
        instance_id = (
            manual["user_product_instance_id"]
            if manual["visibility"] == "private"
            else (owned_instance["id"] if owned_instance else None)
        )
        if instance_id:
            execute(
                connection,
                (
                    "INSERT INTO job_request_links(job_id,user_id,user_product_instance_id) "
                    "VALUES(:j,:u,:i) ON CONFLICT DO NOTHING"
                ),
                j=job["id"],
                u=current["id"],
                i=instance_id,
            )
        result = products.job_view(job)
        remember(connection, current["id"], operation, key, request_body, job["id"], result, 202)
        return result


@app.get("/api/v1/conversations/{conversation_id}")
def get_conversation(conversation_id: UUID, current=Depends(user)):
    with transaction() as connection:
        return chat.conversation_view(
            products.owned(connection, "conversations", conversation_id, current["id"])
        )


@app.get("/api/v1/conversations/{conversation_id}/messages")
def messages(
    conversation_id: UUID,
    current=Depends(user),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: UUID | None = None,
):
    with transaction() as connection:
        products.owned(connection, "conversations", conversation_id, current["id"])
        position = None
        if cursor:
            position = one(
                connection,
                ("SELECT created_at,id FROM messages WHERE id=:id AND conversation_id=:c"),
                id=cursor,
                c=conversation_id,
            )
            if not position:
                missing()
        rows = many(
            connection,
            (
                "SELECT * FROM messages WHERE conversation_id=:c AND (CAST(:cursor AS"
                " uuid) IS NULL OR (created_at,id)>(:created,CAST(:cursor AS uuid))) "
                "ORDER BY created_at,id LIMIT :limit"
            ),
            c=conversation_id,
            cursor=cursor,
            created=position["created_at"] if position else datetime(1970, 1, 1, tzinfo=UTC),
            limit=limit + 1,
        )
        return {
            "items": [chat.message_view(connection, row) for row in rows[:limit]],
            "nextCursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
        }


@app.post("/api/v1/conversations/{conversation_id}/messages", status_code=201)
def send_message(
    conversation_id: UUID,
    body: MessageRequest,
    current=Depends(user),
    key: str = Header(alias="Idempotency-Key"),
    retry: UUID | None = Header(default=None, alias="Retry-Of"),
):
    with transaction() as connection:
        run, created = chat.start_run(
            connection, conversation_id, body, current["id"], request_key(key), retry
        )
        if not created:
            result = chat.run_view(connection, run)
            if result["message"]:
                return result["message"]
            raise DomainError(409, "RESOURCE_CONFLICT", "该请求已有运行，请查询运行状态")
    result = chat.complete_run(run)
    if not result.get("message"):
        raise DomainError(409, "RESOURCE_CONFLICT", "回答已停止")
    return result["message"]


def event(kind, value):
    return f"event: {kind}\ndata: {json_value(value)}\n\n"


@app.post("/api/v1/conversations/{conversation_id}/stream")
def stream(
    conversation_id: UUID,
    body: MessageRequest,
    request: Request,
    current=Depends(user),
    key: str = Header(alias="Idempotency-Key"),
    retry: UUID | None = Header(default=None, alias="Retry-Of"),
):
    with transaction() as connection:
        run, created = chat.start_run(
            connection, conversation_id, body, current["id"], request_key(key), retry
        )
        replay = chat.run_view(connection, run) if not created else None

    async def generate():
        task = None
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        emitted = False
        heartbeat_at = loop.time()
        try:
            yield event("status", {"runId": str(run["id"]), "status": run["status"]})
            if created:

                def emit_token(chunk):
                    loop.call_soon_threadsafe(queue.put_nowait, ("token", {"text": chunk}))

                def emit_status(stage):
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ("status", {"runId": str(run["id"]), "status": stage})
                    )

                task = asyncio.create_task(
                    asyncio.to_thread(chat.complete_run, run, emit_token, emit_status)
                )
                while not task.done() or not queue.empty():
                    try:
                        kind, payload = await asyncio.wait_for(queue.get(), timeout=0.2)
                        emitted = emitted or kind == "token"
                        yield event(kind, payload)
                    except TimeoutError:
                        if loop.time() - heartbeat_at >= 10:
                            with transaction() as connection:
                                execute(
                                    connection,
                                    HEARTBEAT_SQL,
                                    id=run["id"],
                                )
                            heartbeat_at = loop.time()
                            yield ": heartbeat\n\n"
                result = await task
            else:
                result = replay
            message = result.get("message")
            if message:
                if not emitted:
                    yield event("token", {"text": message["content"]})
                for citation in message["citations"]:
                    yield event("citation", citation)
                yield event("done", {"runId": str(run["id"]), "messageId": message["id"]})
            else:
                request.scope["v2_error_code"] = result["status"]
                yield event("status", {"runId": str(run["id"]), "status": result["status"]})
        except DomainError as error:
            request.scope["v2_error_code"] = error.code
            yield event("error", {**error.body(), "runId": str(run["id"])})
        except asyncio.CancelledError:
            request.scope["v2_error_code"] = "interrupted"
            # 后台外部调用可能仍返回；状态栅栏会阻止其覆盖 interrupted。
            with transaction() as connection:
                execute(
                    connection,
                    (
                        "UPDATE assistant_runs SET status='interrupted',finished_at=now() "
                        "WHERE id=:id AND status='running'"
                    ),
                    id=run["id"],
                )
            raise

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/conversations/{conversation_id}/runs/{run_id}")
def get_run(conversation_id: UUID, run_id: UUID, current=Depends(user)):
    with transaction() as connection:
        return chat.run_view(
            connection, chat.get_run(connection, conversation_id, run_id, current["id"])
        )


@app.post("/api/v1/conversations/{conversation_id}/runs/{run_id}/cancel")
def cancel_run(conversation_id: UUID, run_id: UUID, current=Depends(user)):
    with transaction() as connection:
        chat.get_run(connection, conversation_id, run_id, current["id"])
        execute(
            connection,
            (
                "UPDATE assistant_runs SET status='cancelled',finished_at=now() WHERE"
                " id=:id AND status='running'"
            ),
            id=run_id,
        )
        return chat.run_view(
            connection, chat.get_run(connection, conversation_id, run_id, current["id"])
        )


# 最外层观测包括认证/限流失败，SSE正文消费完毕才结算完整耗时。
app.add_middleware(operations.MetricsMiddleware)
