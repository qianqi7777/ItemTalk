"""用户数据删除：先阻止新写入，再有界清理对象，最后从子到父删除私有记录。"""

from uuid import UUID

from app.db import execute, many, one, transaction
from app.errors import DomainError, missing
from app.security import digest
from app.services.products import new_job
from app.services.queue import fenced, finish
from app.storage import Storage


def queue_cleanup(connection, user, scope, resource, asset_ids):
    payload = {"scope": scope, "resourceId": str(resource), "assetIds": [str(a) for a in asset_ids]}
    return new_job(connection, "user_cleanup", payload, user=user, key=f"delete:{scope}:{resource}")


def account(connection, user):
    execute(connection, "DELETE FROM query_embedding_cache WHERE user_id=:u", u=user)
    row = one(connection, "SELECT * FROM users WHERE id=:u FOR UPDATE", u=user)
    if not row or row["status"] != "active" or row["deleted_at"]:
        missing()
    asset_ids = [
        r["id"] for r in many(connection, "SELECT id FROM assets WHERE owner_user_id=:u", u=user)
    ]
    cancel_jobs(connection, user)
    job = queue_cleanup(connection, user, "account", user, asset_ids)
    execute(connection, "UPDATE users SET status='deleting',deleted_at=now() WHERE id=:u", u=user)
    execute(
        connection,
        (
            "UPDATE assets SET status='deleting',deleted_at=COALESCE(deleted_at,now()) "
            "WHERE owner_user_id=:u"
        ),
        u=user,
    )
    execute(
        connection,
        (
            "UPDATE private_objects SET delete_after=LEAST(delete_after,now()) WHERE "
            "owner_user_id=:u AND object_key LIKE 'private/%'"
        ),
        u=user,
    )
    execute(
        connection,
        (
            "UPDATE user_product_instances SET deleted_at=COALESCE(deleted_at,now()) "
            "WHERE user_id=:u"
        ),
        u=user,
    )
    execute(
        connection,
        (
            "UPDATE conversations SET "
            "deleted_at=COALESCE(deleted_at,now()),status='archived' WHERE user_id=:u"
        ),
        u=user,
    )
    execute(
        connection,
        "UPDATE guidance_sessions SET deleted_at=COALESCE(deleted_at,now()) WHERE user_id=:u",
        u=user,
    )
    execute(
        connection,
        (
            "UPDATE user_memories SET "
            "deleted_at=COALESCE(deleted_at,now()),consent_status='revoked' WHERE "
            "user_id=:u"
        ),
        u=user,
    )
    execute(
        connection,
        (
            "UPDATE identification_attempts SET deleted_at=COALESCE(deleted_at,now()) "
            "WHERE user_id=:u"
        ),
        u=user,
    )
    execute(
        connection,
        (
            "UPDATE assistant_runs SET status='cancelled',finished_at=now() WHERE "
            "user_id=:u AND status='running'"
        ),
        u=user,
    )
    return {"cleanupJobId": str(job["id"])}


def cancel_jobs(connection, user, ids=None):
    execute(
        connection,
        (
            "UPDATE jobs SET "
            "status='cancelled',finished_at=now(),lease_token=NULL,lease_until=NULL "
            "WHERE user_id=:u AND status IN ('queued','running','waiting_for_user') AND "
            "(CAST(:ids AS uuid[]) IS NULL OR id=ANY(:ids))"
        ),
        u=user,
        ids=ids,
    )


def scope_rows(connection, user, scope, resource):
    conversations = many(
        connection,
        (
            "SELECT id FROM conversations WHERE user_id=:u AND "
            "(user_product_instance_id=:r OR id=:r)"
        ),
        u=user,
        r=resource,
    )
    guides = (
        many(
            connection,
            "SELECT id FROM guidance_sessions WHERE user_id=:u AND user_product_instance_id=:r",
            u=user,
            r=resource,
        )
        if scope == "instance"
        else []
    )
    conversation_ids = [r["id"] for r in conversations]
    guide_ids = [r["id"] for r in guides]
    message_ids = [
        r["id"]
        for r in many(
            connection,
            "SELECT id FROM messages WHERE user_id=:u AND conversation_id=ANY(:ids)",
            u=user,
            ids=conversation_ids,
        )
    ]
    assets = many(
        connection,
        (
            "SELECT DISTINCT a.id FROM assets a WHERE a.owner_user_id=:u AND "
            "(EXISTS(SELECT 1 FROM message_attachments m WHERE m.asset_id=a.id AND "
            "m.message_id=ANY(:messages)) OR EXISTS(SELECT 1 FROM "
            "guidance_step_attachments ga JOIN guidance_steps gs ON "
            "gs.id=ga.guidance_step_id WHERE ga.asset_id=a.id AND "
            "gs.session_id=ANY(:guides))) AND NOT EXISTS(SELECT 1 FROM "
            "message_attachments ma JOIN messages m ON m.id=ma.message_id WHERE "
            "ma.asset_id=a.id AND NOT (m.conversation_id=ANY(:conversations))) AND NOT "
            "EXISTS(SELECT 1 FROM guidance_step_attachments ga JOIN guidance_steps gs ON"
            " gs.id=ga.guidance_step_id WHERE ga.asset_id=a.id AND NOT "
            "(gs.session_id=ANY(:guides))) AND NOT EXISTS(SELECT 1 FROM "
            "identification_attempt_attachments ia WHERE ia.asset_id=a.id)"
        ),
        u=user,
        messages=message_ids,
        guides=guide_ids,
        conversations=conversation_ids,
    )
    return conversation_ids, guide_ids, [r["id"] for r in assets]


def resource(connection, user, scope, resource_id):
    queries = {
        "instance": "SELECT * FROM user_product_instances WHERE id=:id AND user_id=:u FOR UPDATE",
        "conversation": ("SELECT * FROM conversations WHERE id=:id AND user_id=:u FOR UPDATE"),
        "asset": (
            "SELECT * FROM assets WHERE id=:id AND owner_user_id=:u AND scope='private' FOR UPDATE"
        ),
    }
    row = one(connection, queries[scope], id=resource_id, u=user)
    if not row:
        missing()
    if row["deleted_at"]:
        old = one(
            connection,
            (
                "SELECT id,status FROM jobs WHERE user_id=:u AND job_type='user_cleanup' "
                "AND idempotency_key=:key"
            ),
            u=user,
            key=f"delete:{scope}:{resource_id}",
        )
        if old:
            if old["status"] == "failed":
                execute(
                    connection,
                    (
                        "UPDATE jobs SET status='queued',attempts=0,available_at=now(),"
                        "lease_token=NULL,lease_until=NULL,last_error=NULL WHERE id=:id"
                    ),
                    id=old["id"],
                )
            return
    if scope == "asset":
        used = one(
            connection,
            (
                "SELECT EXISTS(SELECT 1 FROM message_attachments WHERE asset_id=:id UNION "
                "ALL SELECT 1 FROM identification_attempt_attachments WHERE asset_id=:id "
                "UNION ALL SELECT 1 FROM guidance_step_attachments WHERE asset_id=:id) AS "
                "used"
            ),
            id=resource_id,
        )
        if used["used"]:
            raise DomainError(409, "RESOURCE_CONFLICT", "文件仍被识别、问答或操作引导引用")
        conversation_ids, guide_ids, asset_ids = [], [], [resource_id]
    else:
        conversation_ids, guide_ids, asset_ids = scope_rows(connection, user, scope, resource_id)
    execute(
        connection,
        (
            "UPDATE assets SET status='deleting',deleted_at=now() WHERE owner_user_id=:u"
            " AND id=ANY(:ids)"
        ),
        u=user,
        ids=asset_ids,
    )
    execute(
        connection,
        (
            "UPDATE private_objects SET delete_after=LEAST(delete_after,now()) WHERE "
            "owner_user_id=:u AND asset_id=ANY(:ids) AND object_key LIKE 'private/%'"
        ),
        u=user,
        ids=asset_ids,
    )
    execute(
        connection,
        (
            "UPDATE conversations SET deleted_at=now(),status='archived' WHERE "
            "id=ANY(:ids) AND user_id=:u"
        ),
        ids=conversation_ids,
        u=user,
    )
    execute(
        connection,
        "UPDATE guidance_sessions SET deleted_at=now() WHERE id=ANY(:ids) AND user_id=:u",
        ids=guide_ids,
        u=user,
    )
    execute(
        connection,
        (
            "UPDATE user_memories SET deleted_at=now(),consent_status='revoked' WHERE "
            "user_id=:u AND (user_product_instance_id=:r OR source_message_id IN (SELECT"
            " id FROM messages WHERE conversation_id=ANY(:ids)))"
        ),
        u=user,
        r=resource_id,
        ids=conversation_ids,
    )
    execute(
        connection,
        (
            "UPDATE assistant_runs SET status='cancelled',finished_at=now() WHERE "
            "user_id=:u AND conversation_id=ANY(:ids) AND status='running'"
        ),
        u=user,
        ids=conversation_ids,
    )
    if scope == "instance":
        execute(
            connection,
            "UPDATE user_product_instances SET deleted_at=now() WHERE id=:id AND user_id=:u",
            id=resource_id,
            u=user,
        )
    related = private_job_ids(connection, user, resource_id, conversation_ids, guide_ids, asset_ids)
    cancel_jobs(connection, user, related)
    queue_cleanup(connection, user, scope, resource_id, asset_ids)


def private_job_ids(connection, user, resource_id, conversation_ids, guide_ids, asset_ids):
    return [
        r["id"]
        for r in many(
            connection,
            (
                "SELECT id FROM jobs WHERE user_id=:u AND (payload->>'instanceId'=:r OR "
                "payload->>'userProductInstanceId'=:r OR payload->>'resourceId'=:r OR "
                "payload->>'resourceId'=ANY(:conversations) OR "
                "payload->>'resourceId'=ANY(:guides) OR payload->>'resourceId'=ANY(:assets) OR "
                "payload->>'conversationId'=ANY(:conversations) OR "
                "payload->>'sessionId'=ANY(:guides) OR payload->>'assetId'=ANY(:assets) OR "
                "payload->>'sourceMessageId' IN (SELECT id::text FROM messages WHERE "
                "conversation_id=ANY(:cids)))"
            ),
            u=user,
            r=str(resource_id),
            conversations=[str(c) for c in conversation_ids],
            guides=[str(g) for g in guide_ids],
            assets=[str(a) for a in asset_ids],
            cids=conversation_ids,
        )
    ]


def track_upload(connection, asset):
    execute(
        connection,
        (
            "INSERT INTO private_objects(owner_user_id,asset_id,object_key,delete_after)"
            " VALUES(:u,:a,:key,:expires+interval '10 minutes') ON CONFLICT(object_key) "
            "DO NOTHING"
        ),
        u=asset["owner_user_id"],
        a=asset["id"],
        key=asset["object_key"],
        expires=asset["expires_at"],
    )


def reserve_object(asset, key):
    # 预留先独立提交。即使主事务提交失败，对象仍有可重试的清理依据。
    # 外层已持有用户共享锁，这里不再排队同一锁，否则可能与等待中的删除形成死锁。
    with transaction(lock_identity=False) as connection:
        row = one(
            connection,
            (
                "INSERT INTO private_objects(owner_user_id,asset_id,object_key,delete_after)"
                " SELECT owner_user_id,id,:key,now()+interval '25 minutes' FROM assets WHERE"
                " id=:id AND owner_user_id=:u AND deleted_at IS NULL RETURNING *"
            ),
            key=key,
            id=asset["id"],
            u=asset["owner_user_id"],
        )
        if not row:
            missing()
        job = new_job(
            connection,
            "asset_cleanup",
            {"assetId": str(asset["id"]), "objectId": str(row["id"])},
            user=asset["owner_user_id"],
            key=f"orphan:{row['id']}",
        )
        execute(
            connection,
            "UPDATE jobs SET available_at=:due WHERE id=:id",
            due=row["delete_after"],
            id=job["id"],
        )


def requeue(connection, job, due=None):
    # 成功处理一份对象不算失败尝试；仅连续异常消耗重试次数，数量大也能有界推进。
    execute(
        connection,
        (
            "UPDATE jobs SET status='queued',attempts=0,available_at=COALESCE(CAST(:due "
            "AS timestamptz),now()),lease_token=NULL,lease_until=NULL WHERE id=:id"
        ),
        due=due,
        id=job["id"],
    )


def cleanup_upload(job):
    with transaction() as connection:
        fenced(connection, job)
        params = {"u": job["user_id"], "asset": UUID(job["payload"]["assetId"])}
        if "objectId" in job["payload"]:
            obj = one(
                connection,
                (
                    "SELECT * FROM private_objects WHERE id=:id AND owner_user_id=:u AND "
                    "asset_id=:asset FOR UPDATE"
                ),
                id=UUID(job["payload"]["objectId"]),
                **params,
            )
        else:
            obj = one(
                connection,
                (
                    "SELECT * FROM private_objects WHERE object_key=:key AND owner_user_id=:u "
                    "AND asset_id=:asset FOR UPDATE"
                ),
                key=job["payload"]["temporaryKey"],
                **params,
            )
        if not obj or obj["purged_at"]:
            finish(connection, job)
            return
        asset = one(
            connection,
            "SELECT * FROM assets WHERE id=:id AND owner_user_id=:u FOR UPDATE",
            id=obj["asset_id"],
            u=job["user_id"],
        )
        if (
            "objectId" in job["payload"]
            and asset
            and asset["object_key"] == obj["object_key"]
            and asset["deleted_at"] is None
            and asset["status"] == "available"
        ):
            finish(connection, job)
            return
        ready = one(
            connection,
            "SELECT delete_after<=now() AS ready FROM private_objects WHERE id=:id",
            id=obj["id"],
        )["ready"]
        if not ready:
            requeue(connection, job, obj["delete_after"])
            return
        Storage().delete(obj["object_key"])
        execute(connection, "UPDATE private_objects SET purged_at=now() WHERE id=:id", id=obj["id"])
        if asset and asset["status"] == "pending" and asset["object_key"] == obj["object_key"]:
            execute(
                connection,
                "UPDATE assets SET status='deleted',deleted_at=now() WHERE id=:id",
                id=asset["id"],
            )
        finish(connection, job)


def cleanup(job):
    with transaction(exclusive=True) as connection:
        fenced(connection, job)
        user = job["user_id"]
        scope = job["payload"]["scope"]
        resource_id = UUID(job["payload"]["resourceId"])
        if scope not in {"account", "instance", "conversation", "asset"}:
            raise DomainError(409, "RESOURCE_CONFLICT", "清理范围无效")
        asset_ids = [UUID(a) for a in job["payload"]["assetIds"]]
        # 物理清理仅作用于已标记删除的本人对象；主表和清单共同限制范围。
        if scope == "account":
            account_row = one(
                connection,
                (
                    "SELECT id FROM users WHERE id=:u AND status='deleting' AND deleted_at IS "
                    "NOT NULL FOR UPDATE"
                ),
                u=user,
            )
            if not account_row:
                finish(connection, job, "cancelled")
                return
            objects = many(
                connection,
                (
                    "SELECT * FROM private_objects WHERE owner_user_id=:u AND purged_at IS NULL "
                    "ORDER BY delete_after,id FOR UPDATE"
                ),
                u=user,
            )
        else:
            queries = {
                "instance": (
                    "SELECT id FROM user_product_instances WHERE id=:r AND user_id=:u AND "
                    "deleted_at IS NOT NULL"
                ),
                "conversation": (
                    "SELECT id FROM conversations WHERE id=:r AND user_id=:u "
                    "AND deleted_at IS NOT NULL"
                ),
                "asset": (
                    "SELECT id FROM assets WHERE id=:r AND owner_user_id=:u AND scope='private' "
                    "AND deleted_at IS NOT NULL"
                ),
            }
            if not one(connection, queries[scope], r=resource_id, u=user):
                finish(connection, job, "cancelled")
                return
            objects = many(
                connection,
                (
                    "SELECT p.* FROM private_objects p JOIN assets a ON a.id=p.asset_id AND "
                    "a.owner_user_id=p.owner_user_id WHERE p.owner_user_id=:u AND "
                    "p.asset_id=ANY(:ids) AND a.deleted_at IS NOT NULL AND p.purged_at IS NULL "
                    "ORDER BY p.delete_after,p.id FOR UPDATE OF p"
                ),
                u=user,
                ids=asset_ids,
            )
        if objects:
            obj = objects[0]
            due = one(
                connection,
                "SELECT delete_after<=now() AS ready FROM private_objects WHERE id=:id",
                id=obj["id"],
            )["ready"]
            if due:
                Storage().delete(obj["object_key"])
                execute(
                    connection,
                    "UPDATE private_objects SET purged_at=now() WHERE id=:id",
                    id=obj["id"],
                )
                requeue(connection, job)
            else:
                requeue(connection, job, obj["delete_after"])
            return
        if scope == "account":
            erase_account(connection, job)
        else:
            erase_resource(connection, job, scope, resource_id, asset_ids)


def erase_checkpoints(connection, user, instance_ids=None, guide_ids=None):
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        if instance_ids is None:
            execute(
                connection,
                (
                    f"DELETE FROM {table} "  # noqa: S608 - table 来自固定白名单
                    "WHERE split_part(thread_id,':',2)=:u AND "
                    "split_part(thread_id,':',1) IN ('probe','guidance','guidance-plan')"
                ),
                u=str(user),
            )  # noqa: S608
        else:
            execute(
                connection,
                (
                    f"DELETE FROM {table} "  # noqa: S608 - table 来自固定白名单
                    "WHERE split_part(thread_id,':',2)=:u AND "
                    "((split_part(thread_id,':',1)='probe' AND "
                    "split_part(thread_id,':',3)=ANY(:instances)) OR "
                    "(split_part(thread_id,':',1) IN ('guidance','guidance-plan') "
                    "AND split_part(thread_id,':',3)=ANY(:guides)))"
                ),
                u=str(user),
                instances=[str(i) for i in instance_ids],
                guides=[str(g) for g in guide_ids],
            )  # noqa: S608


def erase_conversations(connection, user, conversation_ids):
    execute(
        connection,
        (
            "DELETE FROM user_memories WHERE user_id=:u AND source_message_id IN (SELECT"
            " id FROM messages WHERE conversation_id=ANY(:ids))"
        ),
        u=user,
        ids=conversation_ids,
    )
    execute(
        connection,
        (
            "DELETE FROM message_feedback WHERE user_id=:u AND message_id IN (SELECT id "
            "FROM messages WHERE conversation_id=ANY(:ids))"
        ),
        u=user,
        ids=conversation_ids,
    )
    execute(
        connection,
        (
            "DELETE FROM message_citations WHERE message_id IN (SELECT id FROM messages "
            "WHERE user_id=:u AND conversation_id=ANY(:ids))"
        ),
        u=user,
        ids=conversation_ids,
    )
    execute(
        connection,
        (
            "DELETE FROM message_attachments WHERE user_id=:u AND message_id IN (SELECT "
            "id FROM messages WHERE conversation_id=ANY(:ids))"
        ),
        u=user,
        ids=conversation_ids,
    )
    # 双向 run/message 外键先解除非延迟的一边，再删 run，最后删 message。
    execute(
        connection,
        "UPDATE messages SET run_id=NULL WHERE user_id=:u AND conversation_id=ANY(:ids)",
        u=user,
        ids=conversation_ids,
    )
    execute(
        connection,
        "DELETE FROM assistant_runs WHERE user_id=:u AND conversation_id=ANY(:ids)",
        u=user,
        ids=conversation_ids,
    )
    execute(
        connection,
        "DELETE FROM messages WHERE user_id=:u AND conversation_id=ANY(:ids)",
        u=user,
        ids=conversation_ids,
    )
    execute(
        connection,
        "DELETE FROM conversations WHERE user_id=:u AND id=ANY(:ids)",
        u=user,
        ids=conversation_ids,
    )


def erase_jobs(connection, user, ids):
    execute(
        connection,
        (
            "DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE "
            "user_id=:u AND id=ANY(:ids))"
        ),
        u=user,
        ids=ids,
    )
    execute(
        connection,
        "DELETE FROM job_request_links WHERE user_id=:u AND job_id=ANY(:ids)",
        u=user,
        ids=ids,
    )
    execute(
        connection,
        "UPDATE jobs SET retry_of_job_id=NULL WHERE user_id=:u AND id=ANY(:ids)",
        u=user,
        ids=ids,
    )
    execute(connection, "DELETE FROM jobs WHERE user_id=:u AND id=ANY(:ids)", u=user, ids=ids)


def erase_resource(connection, job, scope, resource_id, asset_ids):
    user = job["user_id"]
    conversation_ids, guide_ids, _ = (
        scope_rows(connection, user, scope, resource_id) if scope != "asset" else ([], [], [])
    )
    messages = many(
        connection,
        "SELECT id FROM messages WHERE user_id=:u AND conversation_id=ANY(:ids)",
        u=user,
        ids=conversation_ids,
    )
    manuals = (
        many(
            connection,
            "SELECT id FROM manuals WHERE owner_user_id=:u AND user_product_instance_id=:r",
            u=user,
            r=resource_id,
        )
        if scope == "instance"
        else []
    )
    runs = many(
        connection,
        "SELECT id FROM assistant_runs WHERE user_id=:u AND conversation_id=ANY(:ids)",
        u=user,
        ids=conversation_ids,
    )
    job_ids = [
        i
        for i in private_job_ids(
            connection, user, resource_id, conversation_ids, guide_ids, asset_ids
        )
        if i != job["id"]
    ]
    ids = [
        resource_id,
        *asset_ids,
        *conversation_ids,
        *guide_ids,
        *job_ids,
        *[r["id"] for r in messages + manuals + runs],
    ]
    execute(
        connection,
        (
            "DELETE FROM api_idempotency_records WHERE user_id=:u AND "
            "(resource_id=ANY(:ids) OR strpos(response_snapshot::text,:resource)>0)"
        ),
        u=user,
        ids=ids,
        resource=str(resource_id),
    )
    erase_checkpoints(connection, user, [resource_id] if scope == "instance" else [], guide_ids)
    execute(
        connection,
        (
            "DELETE FROM guidance_step_attachments WHERE user_id=:u AND guidance_step_id"
            " IN (SELECT id FROM guidance_steps WHERE session_id=ANY(:ids))"
        ),
        u=user,
        ids=guide_ids,
    )
    execute(
        connection,
        "DELETE FROM guidance_sessions WHERE user_id=:u AND id=ANY(:ids)",
        u=user,
        ids=guide_ids,
    )
    if scope == "instance":
        execute(
            connection,
            "DELETE FROM user_memories WHERE user_id=:u AND user_product_instance_id=:r",
            u=user,
            r=resource_id,
        )
        execute(
            connection,
            (
                "DELETE FROM manuals WHERE owner_user_id=:u AND user_product_instance_id=:r "
                "AND visibility='private'"
            ),
            u=user,
            r=resource_id,
        )
        execute(
            connection,
            "DELETE FROM job_request_links WHERE user_id=:u AND user_product_instance_id=:r",
            u=user,
            r=resource_id,
        )
    erase_conversations(connection, user, conversation_ids)
    erase_jobs(connection, user, job_ids)
    execute(
        connection,
        (
            "DELETE FROM private_objects WHERE owner_user_id=:u AND asset_id=ANY(:ids) "
            "AND purged_at IS NOT NULL"
        ),
        u=user,
        ids=asset_ids,
    )
    execute(
        connection,
        "DELETE FROM assets WHERE owner_user_id=:u AND id=ANY(:ids) AND deleted_at IS NOT NULL",
        u=user,
        ids=asset_ids,
    )
    if scope == "instance":
        execute(
            connection,
            (
                "DELETE FROM user_product_instances WHERE id=:id AND user_id=:u AND "
                "deleted_at IS NOT NULL"
            ),
            id=resource_id,
            u=user,
        )
    finish(connection, job)
    anonymize_receipt(connection, job)


def anonymize_receipt(connection, job):
    # 留下非个人化的完成状态；不把原用户ID、资源ID和清理清单转成公共 payload。
    execute(
        connection,
        (
            "UPDATE jobs SET "
            "user_id=NULL,payload='{}',request_hash=:hash,idempotency_key=:key,last_error=NULL"
            " WHERE id=:id"
        ),
        hash=digest({}),
        key=f"cleanup-complete:{job['id']}",
        id=job["id"],
    )


def erase_account(connection, job):
    user = job["user_id"]
    conversation_ids = [
        r["id"] for r in many(connection, "SELECT id FROM conversations WHERE user_id=:u", u=user)
    ]
    erase_checkpoints(connection, user)
    execute(connection, "DELETE FROM api_idempotency_records WHERE user_id=:u", u=user)
    execute(connection, "DELETE FROM user_memories WHERE user_id=:u", u=user)
    execute(connection, "DELETE FROM guidance_step_attachments WHERE user_id=:u", u=user)
    execute(connection, "DELETE FROM guidance_sessions WHERE user_id=:u", u=user)
    execute(
        connection, "DELETE FROM manuals WHERE owner_user_id=:u AND visibility='private'", u=user
    )
    erase_conversations(connection, user, conversation_ids)
    execute(connection, "DELETE FROM identification_attempt_attachments WHERE user_id=:u", u=user)
    execute(
        connection,
        "UPDATE identification_attempts SET supersedes_attempt_id=NULL WHERE user_id=:u",
        u=user,
    )
    execute(connection, "DELETE FROM identification_attempts WHERE user_id=:u", u=user)
    execute(connection, "DELETE FROM job_request_links WHERE user_id=:u", u=user)
    execute(connection, "DELETE FROM private_objects WHERE owner_user_id=:u", u=user)
    execute(connection, "DELETE FROM assets WHERE owner_user_id=:u AND scope='private'", u=user)
    execute(connection, "DELETE FROM user_product_instances WHERE user_id=:u", u=user)
    ids = [
        r["id"]
        for r in many(
            connection, "SELECT id FROM jobs WHERE user_id=:u AND id<>:job", u=user, job=job["id"]
        )
    ]
    erase_jobs(connection, user, ids)
    execute(
        connection,
        "DELETE FROM usage_counters WHERE subject=:u OR subject=:http OR subject=:model",
        u=str(user),
        http=f"http-user:{user}",
        model=f"model:{user}",
    )
    finish(connection, job)
    anonymize_receipt(connection, job)
    execute(connection, "DELETE FROM users WHERE id=:u AND status='deleting'", u=user)
