"""Gemini 功能性指纹探针单元测试（Task 13）。

覆盖：A 组双向判真伪（supported→PASS / 声称却缺特有字段→FAIL/DEGRADED /
上游 400→SKIPPED）、B 组单向确证（supported→PASS / 任何不支持→SKIPPED 不扣分）、
兼容层整组 applicable=False、token 计数一致性双调用、多模态需声明能力、注册自检。

零网络：注入 FakeAdapter（脚本化 feature_flags/usage/count_tokens）。
"""
import json

from app.probes.base import BudgetCounter, ProbeContext, ProbeStatus
from app.probes.gemini_features import (
    GeminiCachingProbe,
    GeminiCodeExecutionProbe,
    GeminiJsonSchemaProbe,
    GeminiLogprobsProbe,
    GeminiMapsGroundingProbe,
    GeminiMultimodalTimestampProbe,
    GeminiSafetyRatingsProbe,
    GeminiSafetySeverityProbe,
    GeminiSearchGroundingProbe,
    GeminiThinkingProbe,
    GeminiTokenConsistencyProbe,
    GeminiUrlContextProbe,
    GeminiVertexRagProbe,
)
from app.probes.registry import ProbeRegistry
from app.providers.base import AdapterResponse, TokenUsage
from app.utils.errors import ErrorCategory, ProbeError
from tests.fixtures.fake_adapter import FakeAdapter


def _gemini(**kwargs) -> FakeAdapter:
    """构造 Gemini 原生伪适配器。"""
    return FakeAdapter(protocol="gemini", access_mode="native", **kwargs)


def _ctx(adapter, **overrides) -> ProbeContext:
    base = {"adapter": adapter, "model_name": "gemini-2.5-pro", "access_mode": "native"}
    base.update(overrides)
    return ProbeContext(**base)


def _resp(**kwargs) -> AdapterResponse:
    kwargs.setdefault("http_status", 200)
    kwargs.setdefault("success", True)
    return AdapterResponse(**kwargs)


# ---------- 适用性：兼容层整组跳过 ----------

async def test_compat_mode_skips_all_a_group():
    adapter = FakeAdapter(
        protocol="gemini", access_mode="openai_compat", chat_response=_resp()
    )
    ctx = _ctx(adapter, access_mode="openai_compat")
    result = await GeminiThinkingProbe().run(ctx)
    assert result.status is ProbeStatus.SKIPPED
    assert "兼容层" in result.evidence["reason"]


async def test_non_gemini_protocol_skipped():
    adapter = FakeAdapter(chat_response=_resp())  # protocol=fake
    result = await GeminiThinkingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED


# ---------- A 组：思考 ----------

async def test_thinking_pass_with_thoughts():
    adapter = _gemini(chat_response=_resp(feature_flags={"thoughts_token_count": 42}))
    result = await GeminiThinkingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS
    assert result.metrics["thoughts_token_count"] == 42


async def test_thinking_fail_without_thoughts():
    adapter = _gemini(chat_response=_resp(content="结果是 476"))
    result = await GeminiThinkingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL  # 声称思考模型却无 thoughtsTokenCount


async def test_thinking_skipped_on_capability_error():
    error = ProbeError(ErrorCategory.CAPABILITY, "thinkingConfig 不支持", http_status=400)
    adapter = _gemini(chat_error=error)
    result = await GeminiThinkingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED  # 版本不支持，不计负分


async def test_thinking_fail_on_non_capability_error():
    adapter = _gemini(chat_error=ProbeError(ErrorCategory.UPSTREAM_5XX, "502"))
    result = await GeminiThinkingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- A 组：代码执行 ----------

async def test_code_execution_pass():
    flags = {"code_execution": [{"executableCode": {}}, {"codeExecutionResult": {}}]}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiCodeExecutionProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_code_execution_degraded_without_result():
    flags = {"code_execution": [{"executableCode": {}}]}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiCodeExecutionProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.DEGRADED


async def test_code_execution_fail_plain_text():
    adapter = _gemini(chat_response=_resp(content="```python\nprint(1)\n```"))
    result = await GeminiCodeExecutionProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- A 组：搜索接地 ----------

async def test_search_grounding_pass():
    flags = {"grounding_metadata": {"groundingChunks": [{"web": {"uri": "x"}}]}}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiSearchGroundingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS
    assert result.metrics["grounding_chunks"] == 1


async def test_search_grounding_degraded_without_chunks():
    flags = {"grounding_metadata": {"webSearchQueries": ["x"]}}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiSearchGroundingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.DEGRADED


async def test_search_grounding_fail_no_metadata():
    adapter = _gemini(chat_response=_resp(content="据我所知……"))
    result = await GeminiSearchGroundingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- A 组：受控结构化输出 ----------

async def test_json_schema_pass():
    content = json.dumps({"city": "北京", "temperature": 20})
    adapter = _gemini(chat_response=_resp(content=content))
    result = await GeminiJsonSchemaProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_json_schema_degraded_missing_key():
    content = json.dumps({"city": "北京"})
    adapter = _gemini(chat_response=_resp(content=content))
    result = await GeminiJsonSchemaProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.DEGRADED
    assert "temperature" in result.evidence["missing_keys"]


async def test_json_schema_fail_non_json():
    adapter = _gemini(chat_response=_resp(content="北京今天 20 度"))
    result = await GeminiJsonSchemaProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- A 组：上下文缓存 ----------

async def test_caching_pass():
    adapter = _gemini(chat_response=_resp(feature_flags={"cached_content_token_count": 128}))
    result = await GeminiCachingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_caching_fail_no_cache_hit():
    adapter = _gemini(chat_response=_resp(feature_flags={"cached_content_token_count": 0}))
    result = await GeminiCachingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- A 组：logprobs ----------

async def test_logprobs_pass():
    flags = {"logprobs_result": {"topCandidates": [{}], "chosenCandidates": [{}]}}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiLogprobsProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_logprobs_degraded_partial():
    flags = {"logprobs_result": {"chosenCandidates": [{}]}}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiLogprobsProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.DEGRADED


async def test_logprobs_fail_absent():
    adapter = _gemini(chat_response=_resp(content="词"))
    result = await GeminiLogprobsProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- A 组：安全评级 ----------

async def test_safety_ratings_pass():
    flags = {"safety_ratings": [{"category": "HARM", "probability": "LOW"}]}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiSafetyRatingsProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_safety_ratings_degraded_malformed():
    flags = {"safety_ratings": [{"foo": "bar"}]}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiSafetyRatingsProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.DEGRADED


async def test_safety_ratings_fail_absent():
    adapter = _gemini(chat_response=_resp(content="你好"))
    result = await GeminiSafetyRatingsProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL


# ---------- A 组：token 计数一致性 ----------

async def test_token_consistency_pass():
    adapter = _gemini(
        chat_response=_resp(usage=TokenUsage(prompt_tokens=20)), count_tokens_value=20
    )
    result = await GeminiTokenConsistencyProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS
    assert result.metrics["deviation"] == 0.0


async def test_token_consistency_fail_large_deviation():
    adapter = _gemini(
        chat_response=_resp(usage=TokenUsage(prompt_tokens=40)), count_tokens_value=20
    )
    result = await GeminiTokenConsistencyProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.FAIL  # 偏差 1.0 > 0.3


async def test_token_consistency_skipped_without_count_method():
    # count_tokens 返回 None 表示端点不可用
    adapter = _gemini(chat_response=_resp(usage=TokenUsage(prompt_tokens=20)))
    result = await GeminiTokenConsistencyProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED


# ---------- A 组：多模态时间戳 ----------

async def test_multimodal_skipped_when_not_declared():
    adapter = _gemini(chat_response=_resp(content="第 3 秒是一只猫"))
    result = await GeminiMultimodalTimestampProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED  # 未声明多模态，不计负分


async def test_multimodal_pass_when_declared():
    adapter = _gemini(chat_response=_resp(content="第 3 秒画面是一辆车"))
    ctx = _ctx(adapter, declared_capabilities={"multimodal"})
    result = await GeminiMultimodalTimestampProbe().run(ctx)
    assert result.status is ProbeStatus.PASS


# ---------- B 组：URL Context（单向确证） ----------

async def test_url_context_pass_confirms():
    ucm = {"urlMetadata": [{"retrievedUrl": "https://x", "urlRetrievalStatus": "OK"}]}
    adapter = _gemini(chat_response=_resp(feature_flags={"url_context_metadata": ucm}))
    result = await GeminiUrlContextProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_url_context_unsupported_skipped_not_fail():
    adapter = _gemini(chat_response=_resp(content="无法访问 URL"))
    result = await GeminiUrlContextProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED  # B 组不支持不扣分


async def test_url_context_error_skipped_not_fail():
    adapter = _gemini(chat_error=ProbeError(ErrorCategory.UPSTREAM_5XX, "502"))
    result = await GeminiUrlContextProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED  # B 组任何错误都不扣分


# ---------- B 组：Vertex RAG / Maps / Severity ----------

async def test_vertex_rag_pass_confirms():
    adapter = _gemini(chat_response=_resp(feature_flags={"retrieval_metadata": [{"x": 1}]}))
    result = await GeminiVertexRagProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_vertex_rag_unsupported_skipped():
    adapter = _gemini(chat_response=_resp(content="无检索"))
    result = await GeminiVertexRagProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED


async def test_maps_grounding_pass_confirms():
    adapter = _gemini(chat_response=_resp(feature_flags={"maps_grounding_metadata": {"x": 1}}))
    result = await GeminiMapsGroundingProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_safety_severity_pass_confirms():
    flags = {"safety_ratings": [{"category": "HARM", "probability": "LOW", "severity": "LOW"}]}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiSafetySeverityProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.PASS


async def test_safety_severity_without_severity_skipped():
    flags = {"safety_ratings": [{"category": "HARM", "probability": "LOW"}]}
    adapter = _gemini(chat_response=_resp(feature_flags=flags))
    result = await GeminiSafetySeverityProbe().run(_ctx(adapter))
    assert result.status is ProbeStatus.SKIPPED


# ---------- 预算守卫 ----------

async def test_feature_probe_skipped_when_budget_exhausted():
    adapter = _gemini(chat_response=_resp(feature_flags={"thoughts_token_count": 1}))
    result = await GeminiThinkingProbe().run(
        _ctx(adapter, budget=BudgetCounter(max_requests=0))
    )
    assert result.status is ProbeStatus.SKIPPED


# ---------- 注册自检 ----------

def test_task13_probes_registered():
    keys = set(ProbeRegistry.all_keys())
    assert {
        "gemini_thinking",
        "gemini_code_execution",
        "gemini_search_grounding",
        "gemini_json_schema",
        "gemini_caching",
        "gemini_logprobs",
        "gemini_safety_ratings",
        "gemini_token_consistency",
        "gemini_multimodal_timestamp",
        "gemini_url_context",
        "gemini_vertex_rag",
        "gemini_maps_grounding",
        "gemini_safety_severity",
    } <= keys
