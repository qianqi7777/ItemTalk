"""独立 Worker 入口；并发固定为 1，不执行模型生成的命令或 SQL。"""

import logging
import signal
import time

from app.adapters import ModelGateway
from app.config import settings
from app.db import actor, execute, json_value, many, one, transaction, validate_runtime
from app.errors import DomainError
from app.services.knowledge import embed_task, parse_task, research
from app.services.products import product_model
from app.services.queue import LeaseHeartbeat, claim, fail, fenced, finish, progress
from app.storage import Storage

logger = logging.getLogger(__name__)


def identify(job):
    progress(job, "identifying", 20)
    with transaction() as connection:
        fenced(connection, job)
        attempt = one(
            connection,
            "SELECT * FROM identification_attempts WHERE id=:id",
            id=job["payload"]["identificationId"],
        )
        user = one(
            connection,
            ("SELECT id FROM users WHERE id=:u AND status='active' AND deleted_at IS NULL"),
            u=attempt["user_id"],
        )
        if not user:
            finish(connection, job, "cancelled")
            return
        execute(
            connection,
            ("UPDATE identification_attempts SET status='processing' WHERE id=:id"),
            id=attempt["id"],
        )
        assets = many(
            connection,
            (
                "SELECT a.* FROM identification_attempt_attachments i JOIN assets a "
                "ON a.id=i.asset_id WHERE i.identification_attempt_id=:id ORDER BY "
                "i.sort_order"
            ),
            id=attempt["id"],
        )
    images = [
        (Storage().read(asset["object_key"], 10 * 1024 * 1024), asset["mime_type"])
        for asset in assets
        if asset["mime_type"].startswith("image/")
    ]
    if images or (attempt["analysis"] or {}).get("visualSession"):
        from app.services.recognition import recognize

        gateway = ModelGateway()
        try:
            with transaction() as connection:
                registered = {
                    row["normalized_name"]: row["official_domains"]
                    for row in many(
                        connection,
                        "SELECT normalized_name,official_domains FROM brands "
                        "WHERE jsonb_array_length(official_domains)>0 "
                        "ORDER BY normalized_name LIMIT 200",
                    )
                }
            output = recognize(
                gateway,
                images,
                attempt["input_text"] or "",
                attempt["analysis"] or {},
                attempt["user_id"],
                on_progress=lambda stage, percent: progress(job, stage, percent),
                registered_domains=registered,
            )
        finally:
            gateway.close()
    elif not attempt["input_text"]:
        output = {"candidates": [], "clarification": "请先转写录音并确认文字，或上传产品照片"}
    else:
        gateway = ModelGateway()
        try:
            output = gateway.identify(attempt["input_text"], [], attempt["user_id"]).model_dump()
            from app.services.recognition import identity_key

            rejected = {
                identity_key(value)
                for value in (attempt["analysis"] or {}).get("rejectedModels", [])
            }
            output["candidates"] = [
                candidate
                for candidate in output["candidates"]
                if identity_key(candidate["brandName"] + candidate["modelCode"]) not in rejected
            ]
            if rejected and not output["candidates"]:
                output["clarification"] = "已排除先前候选，请补充更多信息或手动输入型号。"
        finally:
            gateway.close()
    with transaction() as connection:
        fenced(connection, job)
        candidates = []
        for candidate in output["candidates"]:
            if candidate.get("candidateId"):
                candidates.append(candidate)
                continue
            model = product_model(
                connection,
                candidate["brandName"],
                candidate["modelCode"],
                candidate["region"],
                candidate["hardwareRevision"],
            )
            candidates.append({**candidate, "productModelId": str(model["id"])})
        execute(
            connection,
            (
                "UPDATE identification_attempts SET "
                "status='waiting_for_user',candidates=CAST(:c AS "
                "jsonb),clarification=:clarification,analysis=CAST(:analysis AS jsonb) WHERE id=:id"
            ),
            c=json_value(candidates),
            clarification=output.get("clarification"),
            analysis=json_value(output.get("analysis") or attempt["analysis"] or {}),
            id=attempt["id"],
        )
        finish(connection, job)


def run_once(requested_id=None):
    job = claim(requested_id)
    if not job:
        return False
    try:
        handlers = {
            "identify": identify,
            "research": research,
            "parse": parse_task,
            "embed": embed_task,
            "asset_cleanup": cleanup_asset,
            "manual": generate_manual,
            "memory_extract": extract_memories,
            "checkpoint_cleanup": cleanup_checkpoint,
            "user_cleanup": cleanup_user,
        }
        handler = handlers.get(job["job_type"])
        if not handler:
            raise DomainError(422, "VALIDATION_ERROR", "任务类型未启用")
        # 领取入口只能返回一个已加租约的任务；私有处理始终切换为该任务本人。
        cleanup_job = job["job_type"] in {"user_cleanup", "asset_cleanup", "checkpoint_cleanup"}
        from datetime import UTC, datetime

        from app.operations import metric, snapshot

        metric(
            "queue",
            job["job_type"],
            "claimed",
            max(0, (datetime.now(UTC) - job["available_at"]).total_seconds() * 1000),
        )
        with (
            actor(job["user_id"], allow_deleted=cleanup_job),
            LeaseHeartbeat(job),
            snapshot(job.get("runtime_snapshot")),
        ):
            handler(job)
    except Exception as error:
        # 不记录上游正文、用户原始输入和密钥；对外只保留受控错误文案。
        logger.warning(
            "Job failed: id=%s type=%s error_type=%s",
            job["id"],
            job["job_type"],
            type(error).__name__,
        )
        with actor(job["user_id"], allow_deleted=True):
            fail(job, error.message if isinstance(error, DomainError) else "任务处理失败，请重试")
    return True


def generate_manual(job):
    from app.services.manuals import generate

    generate(job)


def extract_memories(job):
    from app.services.memories import extract

    extract(job)


def cleanup_checkpoint(job):
    from app.services.guidance import cleanup_checkpoint as cleanup

    cleanup(job)


def cleanup_asset(job):
    from app.services.lifecycle import cleanup_upload

    cleanup_upload(job)


def cleanup_user(job):
    from app.services.lifecycle import cleanup

    cleanup(job)


def main():
    if settings().environment in {"production", "staging"}:
        validate_runtime("worker")
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        if settings().v2_enabled:
            with transaction() as connection:
                execute(connection, "SELECT itemtalk_ops_cleanup()")
        if not run_once():
            time.sleep(settings().worker_poll_seconds)


if __name__ == "__main__":
    main()
