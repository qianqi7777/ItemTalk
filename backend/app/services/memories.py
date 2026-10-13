"""用户私有记忆：提取只产生待确认候选，确认且有效后才进入当前实例上下文。"""

import re
from datetime import UTC, datetime
from uuid import UUID

from app.adapters import ModelGateway
from app.db import execute, many, one, transaction
from app.errors import DomainError, missing
from app.security import digest
from app.services.products import new_job, owned
from app.services.queue import fenced, finish


def memory_view(row):
    return {
        "id": str(row["id"]),
        "memoryType": row["memory_type"],
        "scope": row["scope"],
        "userProductInstanceId": str(row["user_product_instance_id"])
        if row["user_product_instance_id"]
        else None,
        "content": row["content"],
        "consentStatus": row["consent_status"],
        "confirmedAt": row["confirmed_at"].isoformat() if row["confirmed_at"] else None,
        "expiresAt": row["expires_at"].isoformat() if row["expires_at"] else None,
    }


def schedule(connection, message, instance_id):
    # 明确表达偏好/个人设置才提取候选，普通问答不额外调用模型。
    # 候选仍须用户确认；来源消息ID去重，回答重试不能重复创建提取任务。
    if not re.search(
        r"记住|我(?:偏好|希望|习惯|喜欢|已经|设置)|以后|请用", message["content"] or ""
    ):
        return None
    key = f"memory:{message['id']}"
    old = one(
        connection,
        (
            "SELECT id FROM jobs WHERE user_id=:u AND "
            "job_type='memory_extract' AND idempotency_key=:key"
        ),
        u=message["user_id"],
        key=key,
    )
    return old or new_job(
        connection,
        "memory_extract",
        {"sourceMessageId": str(message["id"]), "instanceId": str(instance_id)},
        user=message["user_id"],
        key=key,
    )


def extract(job):
    with transaction() as connection:
        fenced(connection, job)
        account = one(
            connection,
            "SELECT id FROM users WHERE id=:u AND status='active' AND deleted_at IS NULL",
            u=job["user_id"],
        )
        if not account:
            finish(connection, job, "cancelled")
            return
        instance = owned(
            connection, "instances", UUID(job["payload"]["instanceId"]), job["user_id"]
        )
        message = one(
            connection,
            "SELECT * FROM messages WHERE id=:id AND user_id=:u AND role='user'",
            id=UUID(job["payload"]["sourceMessageId"]),
            u=job["user_id"],
        )
        if not message:
            finish(connection, job, "cancelled")
            return
        owned(connection, "conversations", message["conversation_id"], job["user_id"])
    candidates = ModelGateway().extract_memories(message["content"] or "", job["user_id"])
    with transaction() as connection:
        fenced(connection, job)
        owned(connection, "instances", instance["id"], job["user_id"])
        owned(connection, "conversations", message["conversation_id"], job["user_id"])
        for candidate in candidates.memories:
            # 提示词不是安全边界；输出还需确定性过滤秘密与联系方式。
            if re.search(
                r"密码|口令|验证码|密钥|secret|token|api[_ -]?key|\S+@\S+|(?:\d[ -]*){7,}",
                candidate.content,
                re.IGNORECASE,
            ):
                continue
            scope = "global" if candidate.memoryType == "preference" else "instance"
            execute(
                connection,
                (
                    "INSERT INTO user_memories(user_id,memory_type,scope,"
                    "user_product_instance_id,source_message_id,content,content_hash,"
                    "expires_at) VALUES (:u,:kind,:scope,:i,:message,:content,:hash,"
                    "now()+interval '30 days') ON CONFLICT DO NOTHING"
                ),
                u=job["user_id"],
                kind=candidate.memoryType,
                scope=scope,
                i=None if scope == "global" else instance["id"],
                message=message["id"],
                content=candidate.content,
                hash=digest(candidate.content),
            )
        finish(connection, job)


def list_for(connection, user, instance_id=None):
    if instance_id:
        owned(connection, "instances", instance_id, user)
    rows = many(
        connection,
        (
            "SELECT mem.* FROM user_memories mem LEFT JOIN "
            "user_product_instances i ON i.id=mem.user_product_instance_id "
            "WHERE mem.user_id=:u AND mem.deleted_at IS NULL AND "
            "(mem.expires_at IS NULL OR mem.expires_at>now()) AND "
            "(mem.scope='global' OR i.deleted_at IS NULL) AND (CAST(:instance "
            "AS uuid) IS NULL OR mem.scope='global' OR "
            "mem.user_product_instance_id=:instance) ORDER BY mem.created_at "
            "DESC,mem.id DESC LIMIT 100"
        ),
        u=user,
        instance=instance_id,
    )
    return {"items": [memory_view(row) for row in rows]}


def confirmed(connection, user, instance_id):
    # 同型号的两台设备也必须隔离设置；只有全局偏好可跨本人产品使用。
    owned(connection, "instances", instance_id, user)
    return many(
        connection,
        (
            'SELECT memory_type AS "memoryType",content FROM user_memories '
            "WHERE user_id=:u AND deleted_at IS NULL AND "
            "consent_status='confirmed' AND (expires_at IS NULL OR "
            "expires_at>now()) AND (scope='global' OR "
            "user_product_instance_id=:i) ORDER BY confirmed_at DESC LIMIT 10"
        ),
        u=user,
        i=instance_id,
    )


def confirm(connection, memory_id, user):
    # 锁住候选防止确认与删除竞争，重复确认不延长原始有效期。
    row = one(
        connection,
        (
            "SELECT * FROM user_memories WHERE id=:id AND user_id=:u AND "
            "deleted_at IS NULL FOR UPDATE"
        ),
        id=memory_id,
        u=user,
    )
    if not row:
        missing()
    if row["user_product_instance_id"]:
        owned(connection, "instances", row["user_product_instance_id"], user)
    if row["expires_at"] and row["expires_at"] <= datetime.now(UTC):
        raise DomainError(409, "RESOURCE_CONFLICT", "记忆已过期，请重新确认新的候选")
    if row["consent_status"] == "confirmed":
        return memory_view(row)
    if row["consent_status"] != "pending":
        raise DomainError(409, "RESOURCE_CONFLICT", "记忆已撤销")
    return memory_view(
        one(
            connection,
            (
                "UPDATE user_memories SET consent_status='confirmed',"
                "confirmed_at=now() WHERE id=:id RETURNING *"
            ),
            id=memory_id,
        )
    )


def delete(connection, memory_id, user):
    row = one(
        connection,
        ("SELECT * FROM user_memories WHERE id=:id AND user_id=:u AND deleted_at IS NULL"),
        id=memory_id,
        u=user,
    )
    if not row:
        missing()
    execute(
        connection,
        ("UPDATE user_memories SET consent_status='revoked',deleted_at=now() WHERE id=:id"),
        id=memory_id,
    )
