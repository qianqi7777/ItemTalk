"""认证、请求校验、计数预算和事务幂等，均由服务端维护。"""

import hashlib
import hmac
import json
import unicodedata
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.config import settings
from app.db import execute, one
from app.errors import DomainError

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def normalize(value):
    return unicodedata.normalize("NFKC", value).strip().casefold()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def verify_password(encoded, plain):
    try:
        return password_hasher.verify(encoded, plain)
    except VerificationError:
        return False


def token_for(user_id, purpose="auth", **extra):
    return jwt.encode(
        {
            "sub": str(user_id),
            "purpose": purpose,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC)
            + timedelta(seconds=settings().auth_ttl_seconds if purpose == "auth" else 900),
            **extra,
        },
        settings().jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_token(token, purpose="auth"):
    try:
        claims = jwt.decode(
            token,
            settings().jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            options={"require": ["sub", "exp", "iat", "purpose"]},
        )
        UUID(claims["sub"])
        if claims["purpose"] != purpose:
            raise ValueError("purpose")
        return claims
    except (jwt.PyJWTError, ValueError):
        raise DomainError(401, "AUTH_REQUIRED", "请重新登录") from None


def origin_of(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def require_write_headers(request):
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise DomainError(403, "FORBIDDEN", "缺少请求校验头")
    if request.headers.get("Origin") != origin_of(settings().app_public_url):
        raise DomainError(403, "FORBIDDEN", "请求来源无效")


def consume(connection, subject, bucket, maximum):
    row = one(
        connection,
        (
            "INSERT INTO usage_counters(subject,bucket,amount) VALUES (:s,:b,1) "
            "ON CONFLICT(subject,bucket) DO UPDATE SET "
            "amount=usage_counters.amount+1 WHERE usage_counters.amount < "
            ":maximum RETURNING amount"
        ),
        s=subject,
        b=bucket,
        maximum=maximum,
    )
    if not row:
        message = "请求额度已用完，请稍后再试"
        if subject == "model:global":
            message = "站内全站日调用额度已用完，请管理员调整额度或等待每日重置"
        elif subject.startswith("model:"):
            message = "站内个人日调用额度已用完，请管理员调整额度或等待每日重置"
        raise DomainError(429, "RATE_LIMITED", message)


def idempotent(connection, user, operation, key, body):
    if not key or len(key) > 160:
        raise DomainError(422, "VALIDATION_ERROR", "需要有效的 Idempotency-Key")
    # Serializes duplicate keys before checking the record and creating business data.
    lock_id = int.from_bytes(
        hashlib.sha256(f"{user}:{operation}:{key}".encode()).digest()[:8], "big", signed=True
    )
    execute(connection, "SELECT pg_advisory_xact_lock(:id)", id=lock_id)
    row = one(
        connection,
        (
            "SELECT * FROM api_idempotency_records WHERE user_id=:u AND "
            "operation=:op AND idempotency_key=:k"
        ),
        u=user,
        op=operation,
        k=key,
    )
    if row and not hmac.compare_digest(row["request_hash"], digest(body)):
        raise DomainError(409, "IDEMPOTENCY_CONFLICT", "同一请求键已用于不同内容")
    return row


def remember(connection, user, operation, key, body, resource, snapshot, status):
    execute(
        connection,
        (
            "INSERT INTO api_idempotency_records "
            "(user_id,operation,idempotency_key,request_hash,resource_id,response_snapshot,response_status,expires_at)"
            " VALUES (:u,:op,:k,:h,:r,CAST(:snapshot AS "
            "jsonb),:status,now()+interval '7 days')"
        ),
        u=user,
        op=operation,
        k=key,
        h=digest(body),
        r=resource,
        snapshot=json.dumps(snapshot, default=str, ensure_ascii=False),
        status=status,
    )
