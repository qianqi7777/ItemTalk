"""产品识别和资料构建服务。

用户确认只证明用户选择了型号；公共资料必须经过独立来源核验。
客户端请求去重与跨用户的公共构建合并分别维护，避免共享任务泄露私有输入。
"""

from uuid import NAMESPACE_OID, uuid4, uuid5

from app.db import execute, json_value, many, one
from app.errors import DomainError, missing
from app.security import digest, normalize

ACTIVE = ("queued", "running", "waiting_for_user")
TERMINAL = ("succeeded", "partial_success", "failed", "cancelled")


def owned(connection, table, resource_id, user_id):
    """仅允许预定义私有实体查询；越权和不存在均返回 404。"""
    account = one(
        connection,
        ("SELECT id FROM users WHERE id=:u AND status='active' AND deleted_at IS NULL"),
        u=user_id,
    )
    if not account:
        raise DomainError(401, "AUTH_REQUIRED", "账号不可用，请重新登录")
    queries = {
        "assets": ("SELECT * FROM assets WHERE id=:id AND owner_user_id=:u AND deleted_at IS NULL"),
        "instances": (
            "SELECT * FROM user_product_instances WHERE id=:id AND user_id=:u AND"
            " deleted_at IS NULL"
        ),
        "identifications": (
            "SELECT * FROM identification_attempts WHERE id=:id AND user_id=:u "
            "AND deleted_at IS NULL"
        ),
        "conversations": (
            "SELECT c.* FROM conversations c JOIN user_product_instances i ON "
            "i.id=c.user_product_instance_id WHERE c.id=:id AND c.user_id=:u AND "
            "c.deleted_at IS NULL AND i.deleted_at IS NULL"
        ),
    }
    row = one(connection, queries[table], id=resource_id, u=user_id)
    if not row:
        missing()
    return row


def require_assets(connection, ids, user):
    """附件必须属于当前用户，且已完成不可变对象确认。"""
    rows = [owned(connection, "assets", asset_id, user) for asset_id in ids]
    if any(row["status"] != "available" for row in rows):
        raise DomainError(409, "RESOURCE_CONFLICT", "请先确认附件上传")
    return rows


def product_model(connection, brand, code, region=None, hardware=None):
    """规范化型号身份；数据库唯一约束同时处理不同 Worker 的竞争。"""
    region = normalize(region).upper() if region else None
    hardware = normalize(hardware) if hardware else None
    brand_row = one(
        connection,
        (
            "INSERT INTO brands(name,normalized_name) VALUES (:name,:normalized) "
            "ON CONFLICT(normalized_name) DO UPDATE SET "
            "normalized_name=EXCLUDED.normalized_name RETURNING *"
        ),
        name=brand,
        normalized=normalize(brand),
    )
    return one(
        connection,
        (
            "INSERT INTO "
            "product_models(brand_id,model_code,normalized_model_code,region,hardware_revision)"
            " VALUES (:b,:code,:normalized,:region,:hardware) ON "
            "CONFLICT(brand_id,normalized_model_code,(COALESCE(region,'')),(COALESCE(hardware_revision,'')))"
            " DO UPDATE SET normalized_model_code=EXCLUDED.normalized_model_code "
            "RETURNING *"
        ),
        b=brand_row["id"],
        code=code,
        normalized=normalize(code),
        region=region,
        hardware=hardware,
    )


def new_job(connection, job_type, payload, user=None, key=None, retry=None):
    from app.operations import capture

    return one(
        connection,
        (
            "INSERT INTO "
            "jobs(user_id,job_type,status,idempotency_key,request_hash,payload,available_at,retry_of_job_id,progress,runtime_snapshot)"
            " VALUES (:u,:type,'queued',:key,:hash,CAST(:payload AS "
            "jsonb),now(),:retry,jsonb_build_object('stage','queued','percent',0),"
            "CAST(:snapshot AS jsonb))"
            " RETURNING *"
        ),
        u=user,
        type=job_type,
        key=key or str(uuid4()),
        hash=digest(payload),
        payload=json_value(payload),
        retry=retry,
        snapshot=json_value(capture(connection)),
    )


def job_view(job):
    """公开任务进度白名单；不返回 payload、租约凭据和上游异常正文。"""
    return {
        "id": str(job["id"]),
        "jobType": job["job_type"],
        "status": job["status"],
        "progress": job["progress"] or {},
        "lastError": job["last_error"],
        "attempts": job["attempts"],
        "maxAttempts": job["max_attempts"],
        "availableAt": job["available_at"].isoformat(),
        "startedAt": job["started_at"].isoformat() if job["started_at"] else None,
        "finishedAt": job["finished_at"].isoformat() if job["finished_at"] else None,
    }


def accessible_job(connection, job_id, user, is_admin=False):
    job = one(
        connection,
        (
            "SELECT j.* FROM jobs j WHERE j.id=:id AND (:admin OR j.user_id=:u OR"
            " EXISTS(SELECT 1 FROM job_request_links l JOIN "
            "user_product_instances i ON i.id=l.user_product_instance_id WHERE "
            "l.job_id=j.id AND l.user_id=:u AND i.deleted_at IS NULL))"
        ),
        id=job_id,
        u=user,
        admin=is_admin,
    )
    if not job:
        missing("JOB_NOT_FOUND")
    return job


def build_for(connection, model_id, instance, user, force=False, retry=None):
    """锁定型号，最多创建一个活动公共构建，并为每个请求方保存归属关联。"""
    if instance:
        owned_instance = owned(connection, "instances", instance, user)
        if str(owned_instance["product_model_id"]) != str(model_id):
            missing()
    model = one(connection, "SELECT * FROM product_models WHERE id=:id FOR UPDATE", id=model_id)
    if not model:
        missing()
    if retry:
        old = one(
            connection,
            (
                "SELECT j.* FROM jobs j JOIN public_builds b ON b.job_id=j.id WHERE "
                "j.id=:retry AND b.product_model_id=:model"
            ),
            retry=retry,
            model=model_id,
        )
        if not old or old["status"] not in TERMINAL:
            raise DomainError(409, "RESOURCE_CONFLICT", "重试任务必须属于该型号且已经结束")
    build = one(
        connection,
        (
            "SELECT j.* FROM public_builds b JOIN jobs j ON j.id=b.job_id WHERE "
            "b.product_model_id=:m AND b.status IN "
            "('queued','running','waiting_for_user')"
        ),
        m=model_id,
    )
    if force and build:
        raise DomainError(409, "RESOURCE_CONFLICT", "活动构建结束后才能刷新")
    if not build and not force and not retry:
        build = one(
            connection,
            (
                "SELECT j.* FROM public_builds b JOIN jobs j ON j.id=b.job_id WHERE "
                "b.product_model_id=:m AND b.status='succeeded' ORDER BY "
                "b.build_revision DESC LIMIT 1"
            ),
            m=model_id,
        )
    if not build:
        revision = one(
            connection,
            (
                "SELECT COALESCE(max(build_revision),0)+1 AS n FROM public_builds "
                "WHERE product_model_id=:m"
            ),
            m=model_id,
        )["n"]
        # 公共 payload 只含型号，不包含发起用户的照片、序列号或原始输入。
        build = new_job(
            connection,
            "research",
            {"productModelId": str(model_id)},
            key=f"build:{model_id}:{revision}",
            retry=retry,
        )
        execute(
            connection,
            (
                "INSERT INTO "
                "public_builds(product_model_id,build_revision,job_id,status) VALUES "
                "(:m,:v,:j,'queued')"
            ),
            m=model_id,
            v=revision,
            j=build["id"],
        )
    if not instance:
        return build
    execute(
        connection,
        (
            "INSERT INTO "
            "job_request_links(job_id,user_id,user_product_instance_id) VALUES "
            "(:j,:u,:i) ON CONFLICT DO NOTHING"
        ),
        j=build["id"],
        u=user,
        i=instance,
    )
    return build


def instance_view(connection, instance):
    model = one(
        connection,
        (
            "SELECT m.*,b.name AS brand_name FROM product_models m JOIN brands b "
            "ON b.id=m.brand_id WHERE m.id=:id"
        ),
        id=instance["product_model_id"],
    )
    build = one(
        connection,
        (
            "SELECT j.* FROM public_builds b JOIN jobs j ON j.id=b.job_id WHERE "
            "b.product_model_id=:m ORDER BY b.build_revision DESC LIMIT 1"
        ),
        m=model["id"],
    )
    status = "not_requested"
    if build:
        status = {
            "queued": "queued",
            "running": (build["progress"] or {}).get("stage", "researching"),
            "waiting_for_user": "partial",
            "succeeded": "ready",
            "partial_success": "partial",
            "failed": "failed",
            "cancelled": "failed",
        }[build["status"]]
        if status not in {
            "queued",
            "researching",
            "parsing",
            "embedding",
            "ready",
            "partial",
            "failed",
        }:
            status = "researching"
    return {
        "id": str(instance["id"]),
        "productModelId": str(model["id"]),
        "nickname": instance["nickname"],
        "modelCode": model["model_code"],
        "brandName": model["brand_name"],
        "identityStatus": model["identity_status"],
        "verificationStatus": model["verification_status"],
        "knowledgeJobId": str(build["id"]) if build else None,
        "knowledgeStatus": status,
    }


def private_choices(attempt):
    """Project saved appearance guesses without paid calls or writes on GET."""
    from app.recognition_models import Comparison, Observation
    from app.services.recognition import identity_key, selectable_candidates

    stored = attempt["candidates"] or []
    analysis = attempt.get("analysis") or {}
    if not analysis.get("observation") or attempt["status"] != "waiting_for_user":
        return stored
    try:
        observation = Observation.model_validate(analysis["observation"])
    except ValueError:
        return stored
    result = selectable_candidates(
        observation, Comparison(), [], analysis, attempt["input_text"] or "", existing=stored
    )
    for candidate in result[len(stored) :]:
        candidate["candidateId"] = str(
            uuid5(
                NAMESPACE_OID,
                str(attempt["id"])
                + ":"
                + identity_key(candidate["brandName"] + candidate["modelCode"]),
            )
        )
    return result


def identification_view(connection, attempt):
    job = one(
        connection,
        (
            "SELECT id,progress FROM jobs WHERE user_id=:u AND "
            "payload->>'identificationId'=:id AND job_type='identify' ORDER BY "
            "created_at DESC LIMIT 1"
        ),
        u=attempt["user_id"],
        id=str(attempt["id"]),
    )
    from app.services.uploads import asset_view

    photos = many(
        connection,
        "SELECT a.* FROM identification_attempt_attachments i JOIN assets a "
        "ON a.id=i.asset_id WHERE i.identification_attempt_id=:id AND a.deleted_at IS NULL "
        "AND a.mime_type LIKE 'image/%' ORDER BY i.sort_order",
        id=attempt["id"],
    )
    analysis = attempt.get("analysis") or {}
    child = one(
        connection,
        "SELECT id FROM identification_attempts WHERE supersedes_attempt_id=:id "
        "AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
        id=attempt["id"],
    )
    return {
        "id": str(attempt["id"]),
        "status": attempt["status"],
        "candidates": private_choices(attempt),
        "clarification": attempt["clarification"],
        "jobId": str(job["id"]),
        "roundNo": attempt.get("round_no", 1),
        "maxRounds": 4,
        "sessionId": str(attempt.get("session_id") or attempt["id"]),
        "supersededBy": str(child["id"]) if child else None,
        "observation": analysis.get("observation"),
        "photoRequests": analysis.get("photoRequests", []),
        "researchStatus": analysis.get("researchStatus", "not_started"),
        "progress": job["progress"] or {},
        "photos": [asset_view(photo) for photo in photos],
    }


def create_instance(connection, model_id, user, nickname=None):
    instance = one(
        connection,
        (
            "INSERT INTO "
            "user_product_instances(user_id,product_model_id,nickname,confirmed_at)"
            " VALUES (:u,:m,:nickname,now()) RETURNING *"
        ),
        u=user,
        m=model_id,
        nickname=nickname,
    )
    build_for(connection, model_id, instance["id"], user)
    return instance


def identify(connection, request, user, key):
    require_assets(connection, request.assetIds, user)
    asset_ids = list(request.assetIds)
    previous, notes, round_no, root = {}, request.inputText, 1, None
    if request.supersedesAttemptId:
        owned(connection, "identifications", request.supersedesAttemptId, user)
        parent = one(
            connection,
            "SELECT * FROM identification_attempts WHERE id=:id FOR UPDATE",
            id=request.supersedesAttemptId,
        )
        require_latest_identification(connection, parent)
        if parent["status"] not in {"waiting_for_user", "failed"}:
            raise DomainError(409, "IDENTIFICATION_NOT_READY", "请等待当前识别完成后再补拍")
        round_no = parent["round_no"] + 1
        if round_no > 4:
            raise DomainError(
                422, "VALIDATION_ERROR", "已达到4轮识别上限，请选择候选、手动输入或重新开始"
            )
        root = parent["session_id"] or parent["id"]
        old_rows = many(
            connection,
            "SELECT i.asset_id,a.mime_type FROM identification_attempt_attachments i "
            "JOIN assets a ON a.id=i.asset_id WHERE identification_attempt_id=:id "
            "ORDER BY i.sort_order",
            id=parent["id"],
        )
        old_assets = [row["asset_id"] for row in old_rows]
        if (
            parent["status"] == "waiting_for_user"
            and not request.discardAssetIds
            and not request.rejectedCandidateIds
            and not (set(asset_ids) - set(old_assets))
            and (not request.inputText or request.inputText == parent["input_text"])
        ):
            raise DomainError(422, "VALIDATION_ERROR", "请添加照片、补充说明或排除候选后再继续判断")
        allowed = set(old_assets) | set(asset_ids)
        if not set(request.discardAssetIds) <= allowed:
            raise DomainError(422, "VALIDATION_ERROR", "只能移除本次识别中的照片")
        asset_ids = [
            value
            for value in dict.fromkeys(old_assets + asset_ids)
            if value not in request.discardAssetIds
        ]
        choices = private_choices(parent)
        valid = {
            row.get("candidateId") or row.get("productModelId"): row
            for row in choices
            if row.get("candidateId") or row.get("productModelId")
        }
        if not {str(value) for value in request.rejectedCandidateIds} <= valid.keys():
            raise DomainError(422, "VALIDATION_ERROR", "只能排除上一轮的候选型号")
        rejected = list((parent["analysis"] or {}).get("rejectedModels", []))
        rejected += [
            f"{valid[str(value)]['brandName']} {valid[str(value)]['modelCode']}"
            for value in request.rejectedCandidateIds
        ]
        previous = {
            "visualSession": (parent["analysis"] or {}).get("visualSession", False)
            or any(row["mime_type"].startswith("image/") for row in old_rows),
            "rejectedModels": list(dict.fromkeys(rejected)),
            "previous": {
                "observation": (parent["analysis"] or {}).get("observation"),
                "candidates": [
                    {
                        key: value
                        for key, value in row.items()
                        if key
                        in {
                            "brandName",
                            "modelCode",
                            "matchReasons",
                            "differences",
                            "missingEvidence",
                        }
                    }
                    for row in choices
                ],
            },
        }
        if parent["input_text"] and request.inputText != parent["input_text"]:
            notes = "\n".join(filter(None, [parent["input_text"], request.inputText]))
    if len(asset_ids) > 10 or (notes and len(notes) > 10000):
        raise DomainError(422, "VALIDATION_ERROR", "识别累计最多10张照片和10000字补充说明")
    if not notes and not asset_ids:
        raise DomainError(422, "VALIDATION_ERROR", "至少保留一张照片或补充说明")
    # Recheck inherited assets too; a previously removed/deleted upload cannot re-enter a round.
    assets = require_assets(connection, asset_ids, user)
    if any(row["mime_type"].startswith("image/") for row in assets):
        previous["visualSession"] = True
    attempt_id = uuid4()
    attempt = one(
        connection,
        (
            "INSERT INTO "
            "identification_attempts(id,user_id,input_text,status,supersedes_attempt_id,session_id,round_no,analysis)"
            " VALUES (:id,:u,:text,'queued',:old,:root,:round,CAST(:analysis AS jsonb)) RETURNING *"
        ),
        u=user,
        id=attempt_id,
        root=root or attempt_id,
        round=round_no,
        analysis=json_value(previous),
        text=notes,
        old=request.supersedesAttemptId,
    )
    for index, asset_id in enumerate(asset_ids):
        execute(
            connection,
            (
                "INSERT INTO "
                "identification_attempt_attachments(identification_attempt_id,asset_id,user_id,sort_order)"
                " VALUES (:id,:asset,:u,:n)"
            ),
            id=attempt["id"],
            asset=asset_id,
            u=user,
            n=index,
        )
    job = new_job(connection, "identify", {"identificationId": str(attempt["id"])}, user, key)
    if previous.get("visualSession"):
        execute(connection, "UPDATE jobs SET max_attempts=1 WHERE id=:id", id=job["id"])
    return identification_view(connection, attempt)


def require_latest_identification(connection, attempt):
    if one(
        connection,
        "SELECT id FROM identification_attempts WHERE supersedes_attempt_id=:id "
        "AND deleted_at IS NULL LIMIT 1",
        id=attempt["id"],
    ):
        raise DomainError(409, "RESOURCE_CONFLICT", "已有更新一轮识别，请使用最新结果")


def confirm(connection, attempt_id, request, user):
    owned(connection, "identifications", attempt_id, user)
    attempt = one(
        connection, ("SELECT * FROM identification_attempts WHERE id=:id FOR UPDATE"), id=attempt_id
    )
    if attempt["status"] != "waiting_for_user":
        raise DomainError(409, "IDENTIFICATION_NOT_READY", "识别结果尚未准备好或已确认")
    require_latest_identification(connection, attempt)
    if request.candidateId:
        candidate = next(
            (
                row
                for row in private_choices(attempt)
                if row.get("candidateId") == str(request.candidateId)
            ),
            None,
        )
        if candidate is None:
            missing()
        model = product_model(
            connection,
            candidate["brandName"],
            candidate["modelCode"],
            candidate.get("region"),
            candidate.get("hardwareRevision"),
        )
    elif request.userProvidedModel:
        provided = request.userProvidedModel
        model = product_model(
            connection,
            provided.brandName,
            provided.modelCode,
            provided.region,
            provided.hardwareRevision,
        )
    else:
        candidates = {item.get("productModelId") for item in attempt["candidates"] or []}
        if str(request.productModelId) not in candidates:
            missing()
        model = one(
            connection, "SELECT * FROM product_models WHERE id=:id", id=request.productModelId
        )
    execute(
        connection,
        (
            "UPDATE product_models SET identity_status='user_confirmed' WHERE "
            "id=:id AND identity_status='candidate'"
        ),
        id=model["id"],
    )
    execute(
        connection,
        (
            "UPDATE identification_attempts SET "
            "status='confirmed',confirmed_product_model_id=:m, "
            "confirmed_at=now(),correction_note=:note,candidates=CAST(:c AS jsonb) WHERE id=:id"
        ),
        m=model["id"],
        note=request.correctionNote,
        c=json_value(private_choices(attempt)),
        id=attempt_id,
    )
    instance = create_instance(connection, model["id"], user)
    return instance_view(connection, instance)


def instances(connection, user):
    return [
        instance_view(connection, row)
        for row in many(
            connection,
            (
                "SELECT * FROM user_product_instances WHERE user_id=:u AND deleted_at"
                " IS NULL ORDER BY created_at DESC"
            ),
            u=user,
        )
    ]
