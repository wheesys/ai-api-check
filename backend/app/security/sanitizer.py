"""错误与日志脱敏（安全规则 3.5 + 设计 §11.2）。

红线：错误 message / raw_excerpt / 日志一律不含 Key、token、Authorization 头；
若上游错误体回显了凭据，落库/记录前先正则剥离。
"""
import re

# 敏感请求头名集合（小写），日志记录前一律剔除其值
SENSITIVE_HEADER_NAMES = frozenset(
    {"authorization", "x-api-key", "api-key", "x-goog-api-key", "cookie"}
)

_REDACTED = "[REDACTED]"

# 规则顺序：更具体的凭据前缀先匹配（sk-ant 先于 sk-），避免残留片段
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"), _REDACTED),  # Anthropic
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), _REDACTED),  # OpenAI 风格
    (re.compile(r"AIza[A-Za-z0-9_\-]{10,}"), _REDACTED),  # Google API key
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}"), f"Bearer {_REDACTED}"),
    # 形如 authorization: xxx / x-api-key=xxx / api_key:"xxx"，仅替换值保留键名
    (
        re.compile(
            r"(?i)(authorization|x-api-key|api[_-]?key)(\"?\s*[:=]\s*\"?)[A-Za-z0-9._\-]{6,}"
        ),
        r"\1\2" + _REDACTED,
    ),
)


class ErrorSanitizer:
    """凭据脱敏工具（无状态，方法均为类方法）。"""

    @classmethod
    def sanitize(cls, text: str | None) -> str | None:
        """剥离文本中的 Key/token/Authorization；None/空串原样返回。"""
        if not text:
            return text
        result = text
        for pattern, replacement in _RULES:
            result = pattern.sub(replacement, result)
        return result

    @classmethod
    def sanitize_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        """返回剔除敏感头值的副本，用于安全日志记录。"""
        return {
            name: (_REDACTED if name.lower() in SENSITIVE_HEADER_NAMES else value)
            for name, value in headers.items()
        }
