"""统一错误分类与脱敏错误模型（设计 §11.1 / §11.2）。

被 HTTP 客户端、探针、引擎共用：将形态各异的上游异常归一为九类，按
"是否可重试 / 是否致命短路"驱动处置；所有面向用户/落库的文本均经脱敏。
"""
from enum import Enum

from app.security.sanitizer import ErrorSanitizer


class ErrorCategory(str, Enum):
    """统一错误类别（设计 §11.1 九类）。"""

    CONNECTIVITY = "connectivity_error"  # DNS/连接拒绝/TLS，致命短路
    AUTH = "auth_error"  # 401/403 Key 失效，致命短路
    QUOTA = "quota_exceeded"  # 402 额度耗尽，致命短路
    RATE_LIMIT = "rate_limit"  # 429 限流，可重试
    TIMEOUT = "timeout"  # 超时，有限重试
    UPSTREAM_5XX = "upstream_5xx"  # 5xx 网关异常，可重试
    PARSE = "parse_error"  # 200 但响应体非法
    CAPABILITY = "capability_unsupported"  # 400 参数/功能不支持，skipped 不计负分
    BUDGET = "budget_exceeded"  # 本地预算护栏触发

    @property
    def retryable(self) -> bool:
        """该类别是否可重试。"""
        return self in _RETRYABLE_CATEGORIES


_RETRYABLE_CATEGORIES = frozenset(
    {ErrorCategory.RATE_LIMIT, ErrorCategory.TIMEOUT, ErrorCategory.UPSTREAM_5XX}
)


def classify_status(http_status: int) -> ErrorCategory | None:
    """按 HTTP 状态码归类；2xx 返回 None（无错误）。"""
    if 200 <= http_status < 300:
        return None
    if http_status in (401, 403):
        return ErrorCategory.AUTH
    if http_status == 402:
        return ErrorCategory.QUOTA
    if http_status == 429:
        return ErrorCategory.RATE_LIMIT
    if http_status in (500, 502, 503, 504):
        return ErrorCategory.UPSTREAM_5XX
    if http_status == 400:
        return ErrorCategory.CAPABILITY
    # 其余 4xx 视为解析/协议层问题，不重试
    return ErrorCategory.PARSE


class ProbeError(Exception):
    """统一探测错误（设计 §11.2）。message/raw_excerpt 构造时即脱敏。"""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        http_status: int | None = None,
        raw_excerpt: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.category = category
        self.retryable = category.retryable
        self.http_status = http_status
        # 落库/外发前强制脱敏，杜绝凭据泄露（安全规则 3.5）
        self.message = ErrorSanitizer.sanitize(message) or ""
        self.raw_excerpt = ErrorSanitizer.sanitize(raw_excerpt)
        self.retry_after = retry_after  # 来自 Retry-After 响应头（秒），可空
        super().__init__(self.message)

    @classmethod
    def from_status(
        cls,
        http_status: int,
        message: str,
        *,
        raw_excerpt: str | None = None,
        retry_after: float | None = None,
    ) -> "ProbeError":
        """由 HTTP 状态码推导类别构造错误。"""
        category = classify_status(http_status) or ErrorCategory.PARSE
        return cls(
            category,
            message,
            http_status=http_status,
            raw_excerpt=raw_excerpt,
            retry_after=retry_after,
        )
