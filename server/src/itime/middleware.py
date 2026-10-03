import logging
import time
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from itime.config import Settings
from itime.errors import AppError, RequestTooLarge, error_response
from itime.observability import request_id_context

logger = logging.getLogger("itime.http")


class RequestEnvelope:
    """纯 ASGI 中间件，不缓冲响应，保留之后 SSE 的背压与断线语义。"""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        request = Request(scope)
        started = False
        status = 500
        size = 0
        start = time.monotonic()

        async def limited_receive() -> Message:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > self.settings.max_request_bytes:
                    raise RequestTooLarge()
            return message

        async def decorated_send(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                has_cache_control = any(
                    key.lower() == b"cache-control"
                    for key, _ in message.get("headers", [])
                )
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != b"x-request-id"
                ]
                headers.extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                    ]
                )
                if not has_cache_control:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        try:
            self.validate_request(request)
            await self.app(scope, limited_receive, decorated_send)
        except Exception as exc:
            if isinstance(exc, AppError):
                error = exc
            else:
                logger.error("request_failed", extra={"error_type": type(exc).__name__})
                error = AppError(
                    "internal_error",
                    "服务暂时无法完成操作。保存状态尚未确认，请稍后重试并保留请求编号。",
                    status=500,
                    retryable=True,
                    saved="unknown",
                )
            if started:
                # 已发送响应头时不能伪造第二个成功响应；安全终止连接，不泄漏原异常。
                raise RuntimeError("response_stream_failed") from None
            await error_response(request, error)(scope, receive, decorated_send)
        finally:
            route = scope.get("route")
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "route": getattr(route, "path", "<unmatched>"),
                    "status": status,
                    "duration_ms": round((time.monotonic() - start) * 1000, 2),
                },
            )
            request_id_context.reset(token)

    def validate_request(self, request: Request) -> None:
        try:
            hostname = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            hostname = None
        if hostname not in self.settings.allowed_hosts:
            raise AppError("invalid_host", "请求主机不在允许范围内。", status=400)

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
            except ValueError:
                length = -1
            if length < 0:
                raise AppError("invalid_length", "请求长度无效。", status=400)
            if length > self.settings.max_request_bytes:
                raise RequestTooLarge()

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (
                origin is not None
                and origin != self.settings.public_origin
                or request.headers.get("sec-fetch-site") == "cross-site"
            ):
                raise AppError("invalid_origin", "请求来源无效，请从 iTime 页面重试。", status=403)
