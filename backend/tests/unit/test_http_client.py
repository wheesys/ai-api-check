"""HTTP 客户端与错误分类单元测试。

零网络：重试逻辑通过注入伪 factory 验证，不发起真实请求。
"""
import pytest

from app.utils.errors import ErrorCategory, ProbeError, classify_status
from app.utils.http_client import HTTPClient, _is_retryable_error


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, None),
        (204, None),
        (400, ErrorCategory.CAPABILITY),
        (401, ErrorCategory.AUTH),
        (403, ErrorCategory.AUTH),
        (402, ErrorCategory.QUOTA),
        (429, ErrorCategory.RATE_LIMIT),
        (500, ErrorCategory.UPSTREAM_5XX),
        (503, ErrorCategory.UPSTREAM_5XX),
        (404, ErrorCategory.PARSE),
    ],
)
def test_classify_status(status, expected):
    """状态码归类符合设计 §11.1。"""
    assert classify_status(status) == expected


@pytest.mark.parametrize(
    "category,retryable",
    [
        (ErrorCategory.RATE_LIMIT, True),
        (ErrorCategory.TIMEOUT, True),
        (ErrorCategory.UPSTREAM_5XX, True),
        (ErrorCategory.AUTH, False),
        (ErrorCategory.QUOTA, False),
        (ErrorCategory.CAPABILITY, False),
        (ErrorCategory.PARSE, False),
    ],
)
def test_category_retryable(category, retryable):
    """可重试性标记正确。"""
    assert category.retryable is retryable
    error = ProbeError(category, "x")
    assert error.retryable is retryable
    assert _is_retryable_error(error) is retryable


def test_probe_error_message_sanitized():
    """构造错误时凭据被脱敏。"""
    error = ProbeError.from_status(
        401, "invalid key sk-ABCDEFGH12345678 rejected", raw_excerpt="sk-ABCDEFGH12345678"
    )
    assert "sk-ABCDEFGH12345678" not in error.message
    assert "sk-ABCDEFGH12345678" not in (error.raw_excerpt or "")
    assert error.category == ErrorCategory.AUTH


async def test_retry_succeeds_after_retryable_failures():
    """可重试错误在限次内重试后成功。"""
    client = HTTPClient(max_retries=2, retry_initial_seconds=0.001, retry_max_seconds=0.002)
    attempts = {"count": 0}

    async def flaky():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ProbeError(ErrorCategory.UPSTREAM_5XX, "502")
        return "ok"

    result = await client._run_with_retry(flaky)
    assert result == "ok"
    assert attempts["count"] == 3  # 失败 2 次 + 成功 1 次


async def test_retry_gives_up_after_max_retries():
    """超过最大重试次数后抛出最后一次错误。"""
    client = HTTPClient(max_retries=2, retry_initial_seconds=0.001, retry_max_seconds=0.002)
    attempts = {"count": 0}

    async def always_fail():
        attempts["count"] += 1
        raise ProbeError(ErrorCategory.RATE_LIMIT, "429")

    with pytest.raises(ProbeError) as exc_info:
        await client._run_with_retry(always_fail)
    assert exc_info.value.category == ErrorCategory.RATE_LIMIT
    assert attempts["count"] == 3  # 1 次初试 + 2 次重试


async def test_non_retryable_raises_immediately():
    """不可重试错误不触发重试，仅执行一次。"""
    client = HTTPClient(max_retries=2, retry_initial_seconds=0.001, retry_max_seconds=0.002)
    attempts = {"count": 0}

    async def auth_fail():
        attempts["count"] += 1
        raise ProbeError(ErrorCategory.AUTH, "401")

    with pytest.raises(ProbeError):
        await client._run_with_retry(auth_fail)
    assert attempts["count"] == 1  # 仅一次，无重试


async def test_request_without_context_raises():
    """未进入异步上下文即调用应给出明确错误。"""
    client = HTTPClient()
    with pytest.raises(RuntimeError, match="未进入上下文"):
        await client.request_json("GET", "http://example.invalid")
