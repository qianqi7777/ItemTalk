"""PostgreSQL 任务队列，使用租约令牌阻止过期 Worker 提交业务结果。"""

from threading import Event, Thread
from uuid import uuid4

from app.config import settings
from app.db import execute, json_value, many, one, transaction
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
                with transaction() as connection:
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
    jobs = many(
        connection,
        (
            "UPDATE jobs SET status=CASE WHEN attempts<max_attempts THEN 'queued'"
            " ELSE 'failed' END, "
            "lease_token=NULL,lease_until=NULL,available_at=now(),last_error='任务租约超时',"
            " finished_at=CASE WHEN attempts>=max_attempts THEN now() ELSE NULL "
            "END WHERE status='running' AND lease_until<now() RETURNING *"
        ),
    )
    for job in jobs:
        synchronize(connection, job["id"], job["status"])
    execute(
        connection,
        (
            "UPDATE assistant_runs SET "
            "status='interrupted',finished_at=now(),last_error='回答连接超时' WHERE "
            "status='running' AND "
            "heartbeat_at<now()-make_interval(secs=>:seconds)"
        ),
        seconds=settings().run_timeout_seconds,
    )


def claim(requested_id=None):
    """正常 Worker 领取最早任务；运维 smoke 可只领取本次新建任务。"""
    with transaction() as connection:
        if requested_id is None:
            recover(connection)
        job = one(
            connection,
            (
                "SELECT * FROM jobs WHERE status='queued' AND available_at<=now() AND"
                " attempts<max_attempts AND (CAST(:requested AS uuid) IS NULL OR id=:requested) "
                "ORDER BY available_at,created_at FOR UPDATE "
                "SKIP LOCKED LIMIT 1"
            ),
            requested=requested_id,
        )
        if not job:
            return None
        job = one(
            connection,
            (
                "UPDATE jobs SET "
                "status='running',attempts=attempts+1,lease_token=:token, "
                "lease_until=now()+make_interval(secs=>:seconds),started_at=COALESCE(started_at,now())"
                " WHERE id=:id RETURNING *"
            ),
            token=uuid4(),
            seconds=settings().lease_seconds,
            id=job["id"],
        )
        synchronize(connection, job["id"], "running")
        return job


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
