"""A shared wall-clock deadline and cancellation check for one external-call pipeline."""

import time
from contextlib import contextmanager
from contextvars import ContextVar

from app.errors import DomainError

_scope = ContextVar("external_call_scope", default=None)


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
