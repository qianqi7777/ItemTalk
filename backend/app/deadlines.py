"""A shared wall-clock deadline and cancellation check for one external-call pipeline."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from contextvars import ContextVar, copy_context

from app.errors import DomainError

_scope = ContextVar("external_call_scope", default=None)
_resolver_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="public-network")
_network_slots = threading.BoundedSemaphore(4)


@contextmanager
def request_scope(deadline, cancelled=lambda: False):
    token = _scope.set((deadline, cancelled))
    try:
        yield
    finally:
        _scope.reset(token)


def remaining(maximum):
    scope = _scope.get()
    if scope is None:
        return maximum
    deadline, cancelled = scope
    if cancelled():
        raise DomainError(409, "RESOURCE_CONFLICT", "回答已停止")
    value = min(maximum, deadline - time.monotonic())
    if value <= 0:
        raise DomainError(504, "ANSWER_TIMEOUT", "联网回答已达到时间限制")
    return max(0.01, value)


def bounded_call(function, *args, on_timeout=None, **kwargs):
    """Bound blocking DNS/header work; at most four active calls, no unbounded queue."""
    deadline = time.monotonic() + remaining(15)
    while not _network_slots.acquire(timeout=min(0.1, remaining(15))):
        if time.monotonic() >= deadline:
            raise DomainError(504, "ANSWER_TIMEOUT", "网络查询已超时")
    context = copy_context()
    future = _resolver_pool.submit(context.run, function, *args, **kwargs)
    future.add_done_callback(lambda _future: _network_slots.release())
    try:
        while True:
            limit = min(remaining(15), deadline - time.monotonic())
            if limit <= 0:
                raise DomainError(504, "ANSWER_TIMEOUT", "网络查询已超时")
            try:
                return future.result(timeout=min(0.1, limit))
            except FutureTimeout:
                if future.done():
                    raise
                continue
    except BaseException:
        future.cancel()
        if on_timeout:
            on_timeout()
        raise
