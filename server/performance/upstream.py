import argparse
import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

logger = logging.getLogger("itime.performance.upstream")


@dataclass(frozen=True)
class UpstreamConfig:
    mode: str = "normal"
    initial_delay_seconds: float = 0.05
    chunk_delay_seconds: float = 0.05
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 0

    def __post_init__(self) -> None:
        if self.mode not in {
            "normal",
            "rate-limit",
            "unavailable",
            "disconnect",
            "timeout",
        }:
            raise ValueError(f"未知协议桩模式：{self.mode}")
        if self.initial_delay_seconds < 0 or self.chunk_delay_seconds < 0:
            raise ValueError("协议桩延迟不能为负数")
        if not 0 <= self.smtp_port <= 65535:
            raise ValueError("SMTP 端口超出范围")


@dataclass
class UpstreamMetrics:
    http_requests: int = 0
    smtp_messages: int = 0


async def _write_smtp_line(writer: asyncio.StreamWriter, value: str) -> None:
    writer.write((value + "\r\n").encode())
    await writer.drain()


async def _smtp_session(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    metrics: UpstreamMetrics,
) -> None:
    in_data = False
    await _write_smtp_line(writer, "220 performance.local ESMTP")
    try:
        while line := await asyncio.wait_for(reader.readline(), timeout=30):
            if len(line) > 65_536:
                await _write_smtp_line(writer, "500 line too long")
                break
            if in_data:
                if line in {b".\r\n", b".\n"}:
                    metrics.smtp_messages += 1
                    in_data = False
                    await _write_smtp_line(writer, "250 accepted")
                continue
            command = line.decode("ascii", errors="ignore").strip().upper()
            if command.startswith(("EHLO ", "HELO ")):
                await _write_smtp_line(writer, "250-performance.local")
                await _write_smtp_line(writer, "250 SIZE 1048576")
            elif command.startswith(("MAIL FROM:", "RCPT TO:")):
                await _write_smtp_line(writer, "250 accepted")
            elif command == "DATA":
                in_data = True
                await _write_smtp_line(writer, "354 end with <CRLF>.<CRLF>")
            elif command in {"RSET", "NOOP"}:
                await _write_smtp_line(writer, "250 ok")
            elif command == "QUIT":
                await _write_smtp_line(writer, "221 bye")
                break
            else:
                await _write_smtp_line(writer, "502 unsupported")
    except (TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _non_streaming_chat(payload: dict) -> dict:
    messages = payload.get("messages", [])
    has_image = any(
        isinstance(message, dict)
        and isinstance(message.get("content"), list)
        for message in messages
    )
    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        function = tools[0].get("function", {})
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "synthetic-call",
                    "type": "function",
                    "function": {
                        "name": function.get("name"),
                        "arguments": '{"marker":"09:00"}',
                    },
                }
            ],
        }
    else:
        content = '{"marker":"09:00"}' if has_image else "ITIME_CHAT_OK"
        message = {"role": "assistant", "content": content}
    return {
        "id": "synthetic-response",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "total_tokens": 13,
        },
    }


def create_app(config: UpstreamConfig | None = None) -> FastAPI:
    active = config or UpstreamConfig()
    metrics = UpstreamMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        smtp_server = None
        if active.smtp_port:
            smtp_server = await asyncio.start_server(
                lambda reader, writer: _smtp_session(reader, writer, metrics),
                active.smtp_host,
                active.smtp_port,
            )
        try:
            yield
        finally:
            if smtp_server is not None:
                smtp_server.close()
                await smtp_server.wait_closed()

    app = FastAPI(
        title="iTime synthetic upstream",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def read_metrics() -> dict[str, int]:
        return {
            "http_requests": metrics.http_requests,
            "smtp_messages": metrics.smtp_messages,
        }

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        started = time.perf_counter()
        metrics.http_requests += 1
        if active.mode == "rate-limit":
            return JSONResponse({"error": {"message": "synthetic"}}, status_code=429)
        if active.mode == "unavailable":
            return JSONResponse({"error": {"message": "synthetic"}}, status_code=503)
        if active.mode == "timeout":
            await asyncio.sleep(3600)

        payload = await request.json()
        if not payload.get("stream"):
            return JSONResponse(
                _non_streaming_chat(payload),
                headers={"X-Request-ID": "synthetic-request"},
            )

        async def stream() -> AsyncIterator[str]:
            await asyncio.sleep(active.initial_delay_seconds)
            for index, part in enumerate(("性能", "测试", "完成")):
                yield _sse(
                    {
                        "id": "synthetic-response",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": part},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
                if active.mode == "disconnect" and index == 0:
                    raise ConnectionResetError("synthetic disconnect")
                await asyncio.sleep(active.chunk_delay_seconds)
            yield _sse(
                {
                    "id": "synthetic-response",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 3,
                        "total_tokens": 13,
                    },
                }
            )
            yield "data: [DONE]\n\n"
            logger.info(
                "synthetic_upstream_response",
                extra={
                    "mode": active.mode,
                    "status": 200,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
            )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Request-ID": "synthetic-request"},
        )

    @app.post("/v1/audio/transcriptions")
    async def transcription(request: Request) -> JSONResponse:
        metrics.http_requests += 1
        await request.body()
        if active.mode == "rate-limit":
            return JSONResponse({"error": {"message": "synthetic"}}, status_code=429)
        if active.mode == "unavailable":
            return JSONResponse({"error": {"message": "synthetic"}}, status_code=503)
        return JSONResponse(
            {
                "text": "合成语音转写完成",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
            headers={"X-Request-ID": "synthetic-request"},
        )

    @app.get("/calendar.ics")
    async def calendar() -> PlainTextResponse:
        metrics.http_requests += 1
        return PlainTextResponse(
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//iTime Performance//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:synthetic-event\r\n"
            "DTSTART:20261005T090000Z\r\n"
            "DTEND:20261005T100000Z\r\n"
            "SUMMARY:Synthetic performance event\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n",
            media_type="text/calendar",
        )

    @app.get("/public.html")
    async def public_page() -> HTMLResponse:
        metrics.http_requests += 1
        return HTMLResponse(
            "<!doctype html><html><head><title>Synthetic</title></head>"
            "<body><main>Synthetic performance document.</main></body></html>"
        )

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 iTime 性能测试协议桩")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--smtp-port", type=int, default=0)
    parser.add_argument(
        "--mode",
        choices=("normal", "rate-limit", "unavailable", "disconnect", "timeout"),
        default="normal",
    )
    parser.add_argument("--initial-delay", type=float, default=0.05)
    parser.add_argument("--chunk-delay", type=float, default=0.05)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = UpstreamConfig(
        mode=args.mode,
        initial_delay_seconds=args.initial_delay,
        chunk_delay_seconds=args.chunk_delay,
        smtp_host=args.host,
        smtp_port=args.smtp_port,
    )
    uvicorn.run(
        create_app(config),
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
