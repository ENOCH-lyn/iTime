import logging
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

logger = logging.getLogger("itime.errors")
SaveState = Literal["not_saved", "saved", "unknown"]


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        retryable: bool = False,
        saved: SaveState = "not_saved",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.saved = saved
        self.details = details or {}


class RequestTooLarge(AppError):
    def __init__(self) -> None:
        super().__init__("request_too_large", "内容超过上传大小限制，请缩小后重试。", status=413)


def error_response(request: Request, error: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=error.status,
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "request_id": request_id,
                "saved": error.saved,
                "retryable": error.retryable,
                "details": error.details,
            }
        },
    )


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic 原始错误带 input，可能含密码/正文；只返回字段位置和错误类别。
        return error_response(
            request,
            AppError(
                "validation_error",
                "输入格式不正确，请检查标记的字段。",
                status=422,
                details={
                    "fields": [
                        {"path": ".".join(map(str, e["loc"])), "type": e["type"]}
                        for e in exc.errors()
                    ]
                },
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(
            request,
            AppError(
                f"http_{exc.status_code}",
                {404: "未找到此内容。", 405: "此操作方式不可用。"}.get(
                    exc.status_code, "请求无法完成。"
                ),
                status=exc.status_code,
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.warning("database_error", extra={"error_type": type(exc).__name__})
        return error_response(
            request,
            AppError(
                "database_unavailable",
                "数据服务暂时不可用。保存状态尚未确认，请恢复连接后使用原操作重试。",
                status=503,
                retryable=True,
                saved="unknown",
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 不输出异常消息/栈局部变量，防止 SQL、URL、凭据与用户正文混入技术访问日志。
        logger.error("unexpected_error", extra={"error_type": type(exc).__name__})
        return error_response(
            request,
            AppError(
                "internal_error",
                "服务暂时无法完成操作。保存状态尚未确认，请稍后重试并保留请求编号。",
                status=500,
                retryable=True,
                saved="unknown",
            ),
        )
