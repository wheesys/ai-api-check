"""全局异常处理（设计 §11.2，落实安全规则 3.5）。

将业务/上游异常归一为带状态码的脱敏 JSON 响应，杜绝 Key/堆栈泄露：
  - ProbeError：按错误类别映射 HTTP 状态码，message 已在构造时脱敏；
  - ValueError：业务校验失败 → 400；
  - 其余未捕获异常：兜底 500，文本经 ErrorSanitizer 脱敏，不回显堆栈。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.security.sanitizer import ErrorSanitizer
from app.utils.errors import ErrorCategory, ProbeError

logger = logging.getLogger(__name__)

# 错误类别 → HTTP 状态码（§11.1）
_CATEGORY_STATUS = {
    ErrorCategory.AUTH: 401,
    ErrorCategory.QUOTA: 402,
    ErrorCategory.RATE_LIMIT: 429,
    ErrorCategory.CAPABILITY: 400,
    ErrorCategory.BUDGET: 400,
    ErrorCategory.PARSE: 502,
    ErrorCategory.CONNECTIVITY: 502,
    ErrorCategory.TIMEOUT: 504,
    ErrorCategory.UPSTREAM_5XX: 502,
}


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""

    @app.exception_handler(ProbeError)
    async def _handle_probe_error(_request: Request, exc: ProbeError) -> JSONResponse:
        http_status = _CATEGORY_STATUS.get(exc.category, 502)
        return JSONResponse(
            status_code=http_status,
            content={"error": exc.category.value, "message": exc.message},
        )

    @app.exception_handler(ValueError)
    async def _handle_value_error(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_request",
                "message": ErrorSanitizer.sanitize(str(exc)) or "请求参数无效",
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # 兜底：脱敏后返回 500，绝不回显堆栈/Key（安全规则 3.5）
        logger.exception("未处理异常：%s", exc.__class__.__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": ErrorSanitizer.sanitize(str(exc)) or "服务器内部错误",
            },
        )
