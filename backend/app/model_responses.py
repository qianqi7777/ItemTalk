"""Responses wire conversion; business validators keep their existing result contract."""

import json
import re
from copy import deepcopy


def prepare_request(payload, provider):
    wire = {
        key: deepcopy(payload[key])
        for key in (
            "model",
            "stream",
            "temperature",
            "top_p",
            "tools",
            "tool_choice",
        )
        if key in payload
    }
    wire["store"] = False
    if "max_tokens" in payload:
        wire["max_output_tokens"] = payload["max_tokens"]
    if provider == "dashscope" and "enable_thinking" in payload:
        wire["enable_thinking"] = payload["enable_thinking"]
    items = []
    for message in payload.get("messages", []):
        role, content = message["role"], message.get("content")
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": content or "",
                }
            )
            continue
        if content:
            if isinstance(content, str):
                content = [
                    {
                        "type": "output_text" if role == "assistant" else "input_text",
                        "text": content,
                    }
                ]
            else:
                converted = []
                for part in content:
                    if part["type"] == "text":
                        converted.append(
                            {
                                "type": "output_text" if role == "assistant" else "input_text",
                                "text": part["text"],
                            }
                        )
                    elif part["type"] == "image_url":
                        converted.append(
                            {
                                "type": "input_image",
                                **part["image_url"],
                                "image_url": part["image_url"]["url"],
                            }
                        )
                        converted[-1].pop("url", None)
                    else:
                        raise ValueError("Unsupported model input")
                content = converted
            items.append({"role": role, "content": content})
        for call in message.get("tool_calls", []):
            items.append({"type": "function_call", "call_id": call["id"], **call["function"]})
    wire["input"] = items
    if "response_format" in payload:
        fmt = deepcopy(payload["response_format"])
        if fmt["type"] == "json_schema" and provider != "dashscope":
            fmt = {"type": "json_schema", **fmt["json_schema"]}
        wire["text"] = {"format": fmt}
    if "tools" in wire:
        wire["tools"] = [
            {"type": "function", **tool["function"]} if tool["type"] == "function" else tool
            for tool in wire["tools"]
        ]
    if isinstance(wire.get("tool_choice"), dict) and wire["tool_choice"].get("type") == "function":
        wire["tool_choice"] = {"type": "function", "name": wire["tool_choice"]["function"]["name"]}
    return wire


def normalize_response(result, provider=None, *, json_output=False):
    if not isinstance(result, dict) or result.get("status") not in {"completed", "incomplete"}:
        raise ValueError("Incomplete Responses envelope")
    incomplete = result.get("status") == "incomplete"
    if incomplete and result.get("incomplete_details", {}).get("reason") != "max_output_tokens":
        raise ValueError("Response not completed")
    texts, calls, searches = [], [], []
    for item in result["output"]:
        kind = item.get("type")
        if kind == "message":
            if item.get("status", "completed") != "completed" and not incomplete:
                raise ValueError("Incomplete output message")
            if item.get("role", "assistant") != "assistant":
                raise ValueError("Unexpected output role")
            for part in item.get("content", []):
                if part.get("type") == "refusal":
                    raise ValueError("Model refused")
                if part.get("type") == "output_text":
                    if not isinstance(part.get("text"), str):
                        raise ValueError("Invalid output text")
                    texts.append(part["text"])
        elif kind == "function_call":
            if item.get("status", "completed") != "completed":
                raise ValueError("Incomplete function call")
            calls.append(
                {
                    "id": item["call_id"],
                    "type": "function",
                    "function": {"name": item["name"], "arguments": item["arguments"]},
                }
            )
        elif kind == "image_search_call":
            if item.get("status") != "completed":
                raise ValueError("Incomplete image search")
            searches.append(item.get("output"))
    text = "".join(texts)
    if calls and any(
        item.get("type") == "reasoning"
        and (item.get("encrypted_content") or (provider != "dashscope" and item.get("id")))
        for item in result["output"]
    ):
        # The local agent replays ordinary messages/functions, not opaque vendor
        # reasoning state. Fail closed; the admin roundtrip probe rejects these
        # configurations instead of approving a model that guidance cannot resume.
        raise ValueError("Stateful reasoning tool continuations are unsupported")
    # Some compatible Qwen gateways prepend an orphan closing marker even with
    # thinking disabled. Discard that marker only; never expose embedded thoughts.
    if text.lstrip().startswith("<think>"):
        raise ValueError("Reasoning appeared in answer content")
    if text.lstrip().startswith("</think>"):
        text = text.lstrip()[len("</think>") :].lstrip()
    if json_output:
        fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", text.strip(), re.S)
        if fence:
            try:
                json.loads(fence[1])
            except ValueError:
                pass  # Preserve the original invalid content for the task's own validator.
            else:
                text = fence[1]
    if not text.strip() and not calls and not searches:
        raise ValueError("Empty response")
    if incomplete:
        if calls or searches:
            raise ValueError("Incomplete tool response")
        # Some tasks parse valid JSON without checking finish_reason. Suppress
        # partial content so none can silently accept an incomplete generation.
        text = ""
    return {
        "choices": [
            {
                "message": {"content": text, **({"tool_calls": calls} if calls else {})},
                "finish_reason": "length" if incomplete else "tool_calls" if calls else "stop",
            }
        ],
        "usage": result.get("usage"),
        "image_search": searches,
    }


def stream_event(value):
    """Only text deltas escape; completion is a Responses event, never [DONE]."""
    if not isinstance(value, dict):
        raise ValueError("Invalid stream event")
    kind = value.get("type")
    if kind in {"error", "response.failed", "response.incomplete", "response.refusal.delta"}:
        raise ValueError("Responses stream failed")
    if kind == "response.output_text.delta":
        if not isinstance(value.get("delta"), str):
            raise ValueError("Invalid text delta")
        return value["delta"], False, None
    if kind == "response.completed":
        result = value["response"]
        if result.get("status") != "completed":
            raise ValueError("Responses stream incomplete")
        normalize_response(result)
        return "", True, result.get("usage")
    return "", False, None


class TextPrefix:
    """Buffer only a possible leading thinking marker, including split SSE chunks."""

    def __init__(self):
        self.pending = ""
        self.started = False

    def push(self, chunk):
        if self.started:
            return chunk
        self.pending += chunk
        candidate = self.pending.lstrip()
        if candidate.startswith("<think>"):
            raise ValueError("Reasoning appeared in answer stream")
        if candidate.startswith("</think>"):
            self.started = True
            return candidate[len("</think>") :]
        if len(self.pending) < 256 and any(
            marker.startswith(candidate) for marker in ("<think>", "</think>")
        ):
            return ""
        self.started = True
        return self.pending
