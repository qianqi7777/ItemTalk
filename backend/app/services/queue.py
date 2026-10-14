"""PostgreSQL 任务队列，使用租约令牌阻止过期 Worker 提交业务结果。"""

from threading import Event, Thread

from app.config import settings
from app.db import actor, execute, json_value, one, transaction
from app.errors import DomainError


class LeaseHeartbeat:
    """外部请求期间续租；条件更新不允许过期或被重新领取的 Worker 复活。"""

    def __init__(self, job):
        self.job = job
        self.stopped = Event()
        self.thread = Thread(target=self._run, daemon=True)

    def _run(self):
        interval = max(1, settings().lease_seconds // 3)
        while not self.stopped.wait(interval):
            try:
                with actor(self.job["user_id"], allow_deleted=True), transaction() as connection:
                    execute(
                        connection,
                        (
                            "UPDATE jobs SET lease_until=now()+make_interval(secs=>:seconds) "
                            "WHERE id=:id AND lease_token=:token AND status='running' AND "
                            "lease_until>now()"
                        ),
                        seconds=settings().lease_seconds,
                        id=self.job["id"],
                        token=self.job["lease_token"],
                    )
            except Exception:
                # 临时数据库故障不能绕过写回栅栏；主处理仍须 fenced 后才能提交。
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stopped.set()
        self.thread.join(timeout=2)


def synchronize(connection, job_id, status):
    execute(connection, "UPDATE public_builds SET status=:s WHERE job_id=:id", s=status, id=job_id)


def recover(connection):
    """回收到期任务和失联问答；达到尝试上限时停止自动重试。"""
    execute(
        connection,
        "SELECT itemtalk_queue_recover(:seconds)",
        seconds=settings().run_timeout_seconds,
    )


def claim(requested_id=None):
    """正常 Worker 领取最早任务；运维 smoke 可只领取本次新建任务。"""
    with transaction() as connection:
        if requested_id is None:
            recover(connection)
        # SECURITY DEFINER 入口只授予 Worker，固定 SQL + SKIP LOCKED，不能列举所有
        # 私有队列或指定任意执行 SQL；API 运行角色无调用权限。
        return one(
            connection,
            "SELECT * FROM itemtalk_queue_claim(CAST(:requested AS uuid),:seconds)",
            requested=requested_id,
            seconds=settings().lease_seconds,
        )


def fenced(connection, job):
    """在业务写入前锁定任务，校验租约仍属于本次领取。"""
    row = one(
        connection,
        (
            "SELECT * FROM jobs WHERE id=:id AND status='running' AND "
            "lease_token=:token AND lease_until>now() FOR UPDATE"
        ),
        id=job["id"],
        token=job["lease_token"],
    )
    if not row:
        raise DomainError(409, "RESOURCE_CONFLICT", "任务租约已失效")
    return row


def progress(job, stage, percent):
    with transaction() as connection:
        fenced(connection, job)
        value = json_value({"stage": stage, "percent": percent})
        execute(
            connection,
            (
                "UPDATE jobs SET progress=CAST(:p AS "
                "jsonb),lease_until=now()+make_interval(secs=>:seconds) WHERE id=:id"
            ),
            p=value,
            seconds=settings().lease_seconds,
            id=job["id"],
        )
        execute(
            connection,
            (
                "INSERT INTO job_events(job_id,event_type,payload) VALUES "
                "(:id,'progress',CAST(:p AS jsonb))"
            ),
            id=job["id"],
            p=value,
        )


def finish(connection, job, status="succeeded", error=None):
    fenced(connection, job)
    execute(
        connection,
        (
            "UPDATE jobs SET "
            "status=:s,last_error=:error,finished_at=now(),lease_token=NULL,lease_until=NULL,"
            " progress=CAST(:progress AS jsonb) WHERE id=:id"
        ),
        s=status,
        error=error,
        id=job["id"],
        progress=json_value({"stage": status, "percent": 100}),
    )
    synchronize(connection, job["id"], status)
    execute(
        connection,
        (
            "INSERT INTO job_events(job_id,event_type,payload) VALUES "
            "(:id,'completed',jsonb_build_object('status',CAST(:s AS text)))"
        ),
        id=job["id"],
        s=status,
    )


def fail(job, error):
    with transaction() as connection:
        try:
            current = fenced(connection, job)
        except DomainError:
            return
        if current["attempts"] >= current["max_attempts"]:
            finish(connection, job, "failed", error)
            if job["job_type"] == "manual":
                execute(
                    connection,
                    "UPDATE manuals SET status='failed' WHERE id=:id",
                    id=job["payload"]["manualId"],
                )
            if job["job_type"] == "identify":
                execute(
                    connection,
                    (
                        "UPDATE identification_attempts SET "
                        "status='failed',clarification=:error WHERE id=:id"
                    ),
                    error=error,
                    id=job["payload"]["identificationId"],
                )
        else:
            execute(
                connection,
                (
                    "UPDATE jobs SET "
                    "status='queued',lease_token=NULL,lease_until=NULL,last_error=:e, "
                    "available_at=now()+make_interval(secs=>:delay) WHERE id=:id"
                ),
                e=error,
                delay=2 ** current["attempts"],
                id=job["id"],
            )
            synchronize(connection, job["id"], "queued")
