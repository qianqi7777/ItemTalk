"""Native search completes at the tool boundary, within one cancellable deadline."""

import json
from concurrent.futures import TimeoutError as FutureTimeout

import httpx
import pytest

from app.errors import DomainError
from tests.test_model_responses import make_gateway


def event(value):
    return ("data: " + json.dumps(value) + "\n\n").encode()


def tool_event(output=None, **changes):
    return {
        "type": "response.output_item.done",
        "item": {
            "type": "image_search_call",
            "status": "completed",
            "output": json.dumps(
                output
                if output is not None
                else [{"title": "Acme Phone 16", "url": "https://images.example/16.jpg"}]
            ),
            **changes,
        },
    }


def test_first_completed_tool_closes_stream_without_waiting_for_more_calls(monkeypatch):
    from app.model_image_search import native_search

    class Body(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield event({"type": "response.reasoning_text.delta", "delta": "private thought"})
            yield event(tool_event())
            pytest.fail("must not wait for a second tool call or final prose")

        def close(self):
            self.closed = True

    body = Body()
    requests = []

    def reply(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["stream"] is True
        assert payload["tool_choice"] == "auto"
        assert "enable_thinking" not in payload
        assert payload["store"] is False
        assert request.extensions["timeout"]["read"] > 60
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

    gateway = make_gateway(monkeypatch, reply, upstream_max_attempts=3)
    rows = native_search(gateway, "data:image/jpeg;base64,AA==", "user")
    assert rows == [{"title": "Acme Phone 16", "url": "https://images.example/16.jpg"}]
    assert len(requests) == 1
    assert body.closed


@pytest.mark.parametrize(
    "value",
    [
        {"type": "response.failed", "response": {"error": {"message": "private"}}},
        {"type": "response.completed", "response": {"output": []}},
        tool_event([], status="completed"),
        tool_event(status="in_progress"),
        tool_event(output="invalid"),
    ],
)
def test_invalid_or_missing_tool_result_fails_without_replay(monkeypatch, value):
    from app.model_image_search import native_search

    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=event(value)
        )

    gateway = make_gateway(monkeypatch, reply, upstream_max_attempts=3)
    with pytest.raises(DomainError) as caught:
        native_search(gateway, "data:image/jpeg;base64,AA==", "user")
    assert "private" not in caught.value.message
    assert len(calls) == 1


def test_model_read_can_wait_longer_than_dns_default_without_losing_deadline(monkeypatch):
    from app import deadlines

    clock = [0.0]
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: clock[0])

    class Future:
        def result(self, timeout):
            clock[0] += timeout
            if clock[0] < 16:
                raise FutureTimeout()
            return b"tool result"

        def add_done_callback(self, callback):
            callback(self)

        def done(self):
            return False

        def cancel(self):
            pass

    monkeypatch.setattr(deadlines._resolver_pool, "submit", lambda *a, **kw: Future())
    with deadlines.request_scope(20):
        assert deadlines.bounded_call(lambda: None, maximum_wait=120) == b"tool result"
    clock[0] = 0
    with deadlines.request_scope(1), pytest.raises(DomainError):
        deadlines.bounded_call(lambda: None, maximum_wait=120)
    assert clock[0] <= 1.01
    clock[0] = 0
    with pytest.raises(DomainError):
        deadlines.bounded_call(lambda: None)
    assert clock[0] <= 15.01


def test_nested_time_limit_preserves_parent_cancellation_and_deadline(monkeypatch):
    from app import deadlines

    stopped = [False]
    monkeypatch.setattr(deadlines.time, "monotonic", lambda: 0)
    with deadlines.request_scope(10, lambda: stopped[0]), deadlines.time_limit(120):
        assert deadlines.remaining(120) == 10
        stopped[0] = True
        with pytest.raises(DomainError) as caught:
            deadlines.remaining(120)
        assert caught.value.status == 409


def test_model_stream_deadline_interrupts_blocked_reader_before_closing_response():
    import threading
    import time

    from app.deadlines import request_scope
    from app.model_transport import ModelResponseStream

    released = threading.Event()

    class Socket:
        def settimeout(self, value):
            pass

        def shutdown(self, how):
            released.set()

    class Connection:
        sock = Socket()

        def close(self):
            pass

    class Response:
        def read1(self, maximum):
            released.wait(1)
            return b""

        def close(self):
            assert released.is_set()

    stream = ModelResponseStream(Connection(), Response(), 120)
    start = time.monotonic()
    with request_scope(start + 0.04), pytest.raises(DomainError):
        list(stream)
    assert time.monotonic() - start < 0.3
