"""运维命令入口，凭据只通过环境读取；真实付费模型验证需要 --paid。"""

import argparse
import json
import re
import time
from pathlib import Path
from uuid import UUID

from langgraph.types import Command

from app.adapters import ModelGateway, SearchGateway
from app.agent_probe import restricted_agent, saver
from app.config import settings
from app.db import execute, json_value, transaction
from app.errors import DomainError
from app.security import normalize
from app.services.knowledge import register_official_source
from app.speech import ASRProvider
from app.storage import sanitize_image


def model_smoke(image_path, audio_path=None):
    """每个角色调用一次；只打印验证结果，不打印供应商响应或图像识别内容。"""
    if settings().model_provider == "mock":
        raise ValueError("Real smoke cannot use mock provider")
    gateway = ModelGateway()
    started = time.perf_counter()
    gateway.budget("smoke")
    response = gateway.request(
        "chat",
        "/chat/completions",
        {
            "model": settings().chat_model,
            "messages": [{"role": "user", "content": "请只用中文说：连接成功"}],
            "max_tokens": 32,
            **({"enable_thinking": False} if settings().model_provider == "dashscope" else {}),
        },
    )
    if not response.get("choices") or not response["choices"][0]["message"].get("content"):
        raise ValueError("Text response invalid")
    print(f"PASS text ({round((time.perf_counter() - started) * 1000)} ms)")
    started = time.perf_counter()
    # 完整消费直到 DONE，避免只收到首个 token 就误判 SSE 验收通过。
    chunks = list(
        gateway.answer_stream(
            "资料中说明如何开机？", [{"rank": 1, "content": "长按电源键 3 秒开机"}], "smoke"
        )
    )
    if not any(chunks):
        raise ValueError("SSE empty")
    print(f"PASS sse ({round((time.perf_counter() - started) * 1000)} ms)")
    if not image_path:
        raise ValueError("Provide an actual nameplate image with --image")
    path = Path(image_path)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Image too large")
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower())
    data, mime = sanitize_image(path.read_bytes(), mime)
    started = time.perf_counter()
    gateway.identify("读取铭牌上的品牌和型号，不猜测", [(data, mime)], "smoke")
    print(f"PASS vision-json ({round((time.perf_counter() - started) * 1000)} ms)")
    started = time.perf_counter()
    assert len(gateway.embed(["产品说明书", "如何开机"], "smoke")[0]) == 1024
    print(f"PASS embedding-1024 ({round((time.perf_counter() - started) * 1000)} ms)")
    started = time.perf_counter()
    SearchGateway().search("产品官方使用手册")
    print(f"PASS search ({round((time.perf_counter() - started) * 1000)} ms)")
    if audio_path:
        audio = Path(audio_path)
        if audio.stat().st_size > 7 * 1024 * 1024:
            raise ValueError("Audio too large")
        mime = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".mp4": "audio/mp4",
            ".webm": "audio/webm",
        }.get(audio.suffix.lower())
        started = time.perf_counter()
        ASRProvider().transcribe(audio.read_bytes(), mime, "smoke")
        print(f"PASS asr ({round((time.perf_counter() - started) * 1000)} ms)")
    else:
        print("SKIP asr: no --audio sample")


def main():
    parser = argparse.ArgumentParser(description="ItemTalk controlled operations")
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--paid", action="store_true")
    smoke.add_argument("--image")
    smoke.add_argument("--audio")
    trust = commands.add_parser("trust-brand")
    trust.add_argument("--name", required=True)
    trust.add_argument("--domains", nargs="+", required=True)
    source = commands.add_parser("register-source")
    source.add_argument("--brand", required=True)
    source.add_argument("--url", required=True)
    probe = commands.add_parser("agent-probe")
    probe.add_argument("--user-id", type=UUID, required=True)
    probe.add_argument("--instance-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        if args.command == "smoke":
            if not args.paid:
                parser.error("Real API calls require --paid")
            model_smoke(args.image, args.audio)
        elif args.command == "trust-brand":
            if not all(
                re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", domain) for domain in args.domains
            ):
                parser.error("Only lowercase DNS domain names are allowed")
            with transaction() as connection:
                execute(
                    connection,
                    (
                        "INSERT INTO brands(name,normalized_name,official_domains) VALUES "
                        "(:name,:normalized,CAST(:domains AS jsonb)) ON "
                        "CONFLICT(normalized_name) DO UPDATE SET "
                        "official_domains=EXCLUDED.official_domains"
                    ),
                    name=args.name,
                    normalized=normalize(args.name),
                    domains=json_value(args.domains),
                )
            print("Trusted domain registry updated")
        elif args.command == "register-source":
            if len(args.url) > 2000:
                parser.error("Source URL exceeds length limit")
            with transaction() as connection:
                register_official_source(connection, args.brand, args.url)
            print("Pending official source registered; content verification still required")
        else:
            config = {
                "configurable": {"thread_id": f"probe:{args.user_id}:{args.instance_id}"},
                "recursion_limit": 8,
            }
            from app.db import actor

            with actor(args.user_id), saver() as checkpointer:
                agent = restricted_agent(checkpointer, args.user_id, args.instance_id)
                first = agent.invoke(
                    {
                        "messages": [
                            {"role": "user", "content": "只调用 read_evidence 读取产品资料"}
                        ]
                    },
                    config,
                )
                if not first.get("__interrupt__"):
                    raise ValueError("Agent did not pause at controlled tool")
                result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
                print(json.dumps({"checkpointResume": bool(result["messages"])}))
    except (DomainError, ValueError):
        parser.exit(
            1,
            "Validation failed; check role configuration, model capability "
            "and service availability.\n",
        )


if __name__ == "__main__":
    main()
