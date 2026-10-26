"""V2运维边界：只接收受控指标，不保存用户输入、供应商正文或凭据。"""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.db import actor, actor_id, execute, json_value, many, one, transaction
from app.errors import DomainError

log = logging.getLogger(__name__)
active_snapshot = ContextVar("runtime_snapshot", default=None)


class RuntimeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedVersion: int = Field(ge=1)
    globalDailyLimit: int = Field(ge=1, le=1000000)
    subjectDailyLimit: int = Field(ge=1, le=1000000)
    backgroundPaused: bool
    chatProfile: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")


def is_admin(connection):
    return settings().v2_enabled and one(connection, "SELECT itemtalk_is_admin() AS value")["value"]


def require_admin(connection):
    if not is_admin(connection):
        raise DomainError(403, "FORBIDDEN", "需要管理员权限")


def runtime(connection):
    config = settings()
    row = (
        one(connection, "SELECT * FROM runtime_config WHERE singleton")
        if config.v2_enabled
        else None
    ) or {
        "version": 1,
        "global_limit": None,
        "subject_limit": None,
        "background_paused": False,
        "chat_profile": "current",
    }
    return {
        "version": row["version"],
        "globalDailyLimit": row["global_limit"] or config.daily_global_model_call_limit,
        "subjectDailyLimit": row["subject_limit"] or config.daily_model_call_limit,
        "backgroundPaused": row["background_paused"],
        "chatProfile": row["chat_profile"],
    }


def capture(connection):
    if not settings().v2_enabled:
        return None
    if active_snapshot.get() is not None:
        return active_snapshot.get()
    row = runtime(connection)
    profile = row["chatProfile"]
    if profile == "current":
        model = settings().chat_model
    elif profile.startswith("managed_"):
        from app.model_configs import get_config

        model = get_config(connection, profile)["model"]
    else:
        selected = settings().model_profiles.get(profile)
        if selected is None or not selected.accepted:
            raise DomainError(503, "UPSTREAM_MODEL_ERROR", "当前模型方案不可用，请管理员回退")
        model = selected.model
    return {"version": row["version"], "chatProfile": profile, "chatModel": model}


@contextmanager
def snapshot(value):
    token = active_snapshot.set(value)
    try:
        yield
    finally:
        active_snapshot.reset(token)


def profiles(connection=None):
    result = [{"id": "current", "model": settings().chat_model, "accepted": True}] + [
        {"id": key, "model": row.model, "accepted": row.accepted}
        for key, row in settings().model_profiles.items()
        if key != "current"
    ]
    if connection is not None:
        from app.model_configs import list_configs

        result += [
            {"id": row["id"], "model": row["model"], "name": row["name"], "accepted": True}
            for row in list_configs(connection)
        ]
    return result


def update_runtime(connection, request):
    require_admin(connection)
    if (
        request.globalDailyLimit > settings().runtime_global_hard_limit
        or request.subjectDailyLimit > settings().runtime_subject_hard_limit
    ):
        raise DomainError(422, "VALIDATION_ERROR", "额度不能超过服务器硬上限")
    if request.chatProfile not in {p["id"] for p in profiles(connection) if p["accepted"]}:
        raise DomainError(422, "VALIDATION_ERROR", "模型方案尚未验收")
    if request.chatProfile.startswith("managed_"):
        from app.model_configs import resolve_configuration

        resolve_configuration(connection, settings(), request.chatProfile)
    changed = one(
        connection,
        "SELECT itemtalk_update_runtime(:v,:g,:s,:p,:m) AS value",
        v=request.expectedVersion,
        g=request.globalDailyLimit,
        s=request.subjectDailyLimit,
        p=request.backgroundPaused,
        m=request.chatProfile,
    )
    if changed["value"] is None:
        raise DomainError(409, "RESOURCE_CONFLICT", "配置已被修改，请刷新后重试")
    return runtime(connection)


def integer_usage(usage, *keys):
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**12:
            return value
    return None


def token_cost(provider, model, usage):
    usage = usage if isinstance(usage, dict) else {}
    it = integer_usage(usage, "prompt_tokens", "input_tokens")
    ot = integer_usage(usage, "completion_tokens", "output_tokens")
    rates = [
        r
        for r in settings().model_prices
        if r.provider == provider
        and r.model == model
        and r.effective_from <= datetime.now(UTC).date()
    ]
    if not rates or it is None:
        return it, ot, None, "", ""
    latest = max(rate.effective_from for rate in rates)
    eligible = [
        rate
        for rate in rates
        if rate.effective_from == latest
        and rate.min_input_tokens <= it
        and (rate.max_input_tokens is None or it <= rate.max_input_tokens)
    ]
    if len(eligible) != 1:
        return it, ot, None, "", ""
    rate = eligible[0]
    if ot is None and rate.output_per_million != 0:
        return it, ot, None, "", ""
    # 输入计费的向量API可以估算费用，但缺失的输出token仍保存NULL，不伪造零。
    cost = (it * rate.input_per_million + (ot or 0) * rate.output_per_million) / Decimal(1000000)
    return it, ot, cost, rate.currency, rate.effective_from.isoformat()


def metric(
    kind, operation, outcome, duration_ms, *, provider="", model="", usage=None, first_token_ms=None
):
    if not settings().v2_enabled:
        return
    # 只允许固定路由模板/角色/错误代码，调用者不得传入URL、问题、响应或用户ID。
    values = [operation, outcome, provider, model]
    if any(
        not isinstance(v, str) or len(v) > maximum
        for v, maximum in zip(values, [160, 40, 40, 100], strict=True)
    ):
        raise ValueError("Invalid metric dimensions")
    if not math.isfinite(duration_ms) or duration_ms < 0:
        raise ValueError("Invalid metric duration")
    it, ot, cost, currency, price_version = token_cost(provider, model, usage)
    try:
        # 无身份的固定写入入口只接受聚合维度；账号删除后返回的上游用量仍须记账。
        with actor(None), transaction(lock_identity=False) as connection:
            execute(
                connection,
                "SELECT itemtalk_record_metric(:k,:op,:p,:m,:o,:ms,:f,:it,:ot,:c,:cur,:pv)",
                k=kind,
                op=operation,
                p=provider,
                m=model,
                o=outcome,
                ms=duration_ms,
                f=first_token_ms,
                it=it,
                ot=ot,
                c=cost,
                cur=currency,
                pv=price_version,
            )
    except Exception:
        # 指标故障不能伪装为写入成功；受控日志供诊断，不输出数据库参数。
        log.error("V2 metric persistence failed: kind=%s", kind)


def query_vector(gateway, question, subject):
    user = actor_id.get()
    config = settings()
    if not config.v2_enabled or user is None:
        return gateway.embed([question], subject)[0]
    key = hmac.new(
        config.jwt_secret.get_secret_value().encode(),
        json.dumps(
            [
                str(user),
                question,
                config.embedding_provider,
                config.embedding_model,
                config.embedding_revision,
                config.embedding_dimensions,
            ],
            ensure_ascii=False,
        ).encode(),
        hashlib.sha256,
    ).hexdigest()
    with transaction() as connection:
        cached = one(
            connection,
            "SELECT embedding::text AS vector FROM query_embedding_cache "
            "WHERE user_id=:u AND cache_key=:k AND expires_at>now()",
            u=user,
            k=key,
        )
    if cached:
        metric("cache", "query_embedding", "hit", 0)
        return json.loads(cached["vector"])
    vector = gateway.embed([question], subject)[0]
    with transaction() as connection:
        # 只在写缓存的短事务内串行化容量检查，不在外部模型调用期间占连接/锁。
        lock_id = int.from_bytes(
            hashlib.sha256(b"v2-cache-cap" + user.bytes).digest()[:8], "big", signed=True
        )
        execute(connection, "SELECT pg_advisory_xact_lock(:key)", key=lock_id)
        # 每个用户有界；缓存与账号外键联动，账号删除不能留下查询向量。
        execute(
            connection,
            "DELETE FROM query_embedding_cache WHERE user_id=:u AND cache_key IN "
            "(SELECT cache_key FROM query_embedding_cache WHERE user_id=:u "
            "ORDER BY expires_at DESC OFFSET 255)",
            u=user,
        )
        execute(
            connection,
            "INSERT INTO query_embedding_cache(user_id,cache_key,embedding,expires_at) "
            "VALUES(:u,:k,CAST(:v AS vector),now()+make_interval(secs=>:ttl)) "
            "ON CONFLICT(user_id,cache_key) DO UPDATE SET embedding=EXCLUDED.embedding,"
            "expires_at=EXCLUDED.expires_at",
            u=user,
            k=key,
            v=json_value(vector),
            ttl=config.query_cache_ttl_seconds,
        )
    metric("cache", "query_embedding", "miss", 0)
    return vector


def metrics(connection, days):
    require_admin(connection)
    # 看板仅支持30天以内，因此P95从原始样本计算，绝不平均每日P95。
    return many(
        connection,
        "SELECT kind,operation,provider,model,outcome,currency,count(*) AS count,"
        "percentile_cont(0.95) WITHIN GROUP(ORDER BY duration_ms) AS p95_ms,"
        "avg(duration_ms) AS mean_ms,avg(first_token_ms) AS first_token_ms,"
        "count(input_tokens) AS input_known,sum(input_tokens) AS input_tokens,"
        "count(output_tokens) AS output_known,sum(output_tokens) AS output_tokens,"
        "count(estimated_cost) AS priced_calls,sum(estimated_cost) AS estimated_cost "
        "FROM telemetry_events WHERE created_at>=now()-make_interval(days=>:d) "
        "GROUP BY kind,operation,provider,model,outcome,currency "
        "ORDER BY kind,operation,provider,model,outcome,currency",
        d=days,
    )


def overview(connection, days):
    groups = metrics(connection, days)
    return {
        "groups": groups,
        "queue": one(connection, "SELECT itemtalk_admin_queue_stats() AS value")["value"],
        "runtime": runtime(connection),
        "modelProfiles": profiles(connection),
        "hardLimits": {
            "global": settings().runtime_global_hard_limit,
            "subject": settings().runtime_subject_hard_limit,
        },
        "trend": many(
            connection,
            "SELECT date_trunc('hour',created_at) AS hour,kind,count(*) AS count,"
            "percentile_cont(0.95) WITHIN GROUP(ORDER BY duration_ms) AS p95_ms "
            "FROM telemetry_events WHERE created_at>=now()-make_interval(days=>:d) "
            "GROUP BY 1,2 ORDER BY 1,2",
            d=days,
        ),
        "costNotice": "费用为配置单价估算，不等于云账单；未知用量和未计价调用单独显示。",
    }


class MetricsMiddleware:
    """在最后一个SSE正文帧后记完成耗时，断开/错误也只结算一次。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not settings().v2_enabled:
            return await self.app(scope, receive, send)
        started, first, status = time.perf_counter(), None, 500

        async def observed(message):
            nonlocal first, status
            if message["type"] == "http.response.start":
                status = message["status"]
                scope["response_content_type"] = (
                    dict(message.get("headers", [])).get(b"content-type", b"").decode()
                )
            if message["type"] == "http.response.body" and message.get("body") and first is None:
                # SSE status/heartbeat不等于可展示正文；token单独在传输层标记。
                if b"event: token" in message["body"] or "text/event-stream" not in str(
                    scope.get("response_content_type", "")
                ):
                    first = (time.perf_counter() - started) * 1000
            await send(message)

        try:
            await self.app(scope, receive, observed)
        except asyncio.CancelledError:
            scope.setdefault("v2_error_code", "interrupted")
            raise
        except Exception:
            scope.setdefault("v2_error_code", "INTERNAL_ERROR")
            raise
        finally:
            route = scope.get("route")
            operation = f"{scope['method']} {getattr(route, 'path', 'unmatched')}"
            # 在线管理页的轮询不反过来刷大业务曲线。
            if not operation.startswith(("GET /api/v1/admin/", "GET /api/v1/health")):
                metric(
                    "http",
                    operation,
                    scope.get("v2_error_code", str(status)),
                    (time.perf_counter() - started) * 1000,
                    first_token_ms=first,
                )
