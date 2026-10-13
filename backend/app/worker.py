"""独立 Worker 入口；并发固定为 1，不执行模型生成的命令或 SQL。"""

import logging
import signal
import time

from app.adapters import ModelGateway
from app.config import settings
from app.db import execute, json_value, many, one, transaction
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
    if not images and not attempt["input_text"]:
        output = {"candidates": [], "clarification": "请先转写录音并确认文字，或上传产品照片"}
    else:
        output = (
            ModelGateway().identify(attempt["input_text"], images, attempt["user_id"]).model_dump()
        )
    with transaction() as connection:
        fenced(connection, job)
        candidates = []
        for candidate in output["candidates"]:
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
                "jsonb),clarification=:clarification WHERE id=:id"
            ),
            c=json_value(candidates),
            clarification=output.get("clarification"),
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
        }
        handler = handlers.get(job["job_type"])
        if not handler:
            raise DomainError(422, "VALIDATION_ERROR", "任务类型未启用")
        with LeaseHeartbeat(job):
            handler(job)
    except Exception as error:
        # 不记录上游正文、用户原始输入和密钥；对外只保留受控错误文案。
        logger.warning(
            "Job failed: id=%s type=%s error_type=%s",
            job["id"],
            job["job_type"],
            type(error).__name__,
        )
        fail(job, error.message if isinstance(error, DomainError) else "任务处理失败，请重试")
    return True


def cleanup_asset(job):
    """过期临时上传清理具备幂等性，确认后的不可变对象不受影响。"""
    with transaction() as connection:
        fenced(connection, job)
        asset = one(
            connection, "SELECT * FROM assets WHERE id=:id FOR UPDATE", id=job["payload"]["assetId"]
        )
        key = job["payload"]["temporaryKey"]
        if not key.startswith("temporary/"):
            raise DomainError(409, "RESOURCE_CONFLICT", "临时清理对象范围无效")
        Storage().delete(key)
        if asset and asset["status"] == "pending":
            execute(
                connection,
                ("UPDATE assets SET status='deleted',deleted_at=now() WHERE id=:id"),
                id=asset["id"],
            )
        finish(connection, job)


def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        if not run_once():
            time.sleep(settings().worker_poll_seconds)


if __name__ == "__main__":
    main()
