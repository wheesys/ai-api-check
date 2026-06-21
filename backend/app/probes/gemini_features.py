"""Gemini 功能性指纹探针（设计 §7.5 / §8.7，仅 access_mode=native）。

核心思想：真正的 Gemini 必然能正确使用 Gemini 独有功能并返回该功能**特有的结构化
字段**，套壳成非 Gemini 模型（GPT/Claude/开源）给不出。本模块主动调用这些功能并
校验特有字段，据此判定"是不是真 Gemini"：

  - A 组（双向判真伪）：支持→证真(PASS)；声称支持却给不出特有字段→证伪(FAIL/DEGRADED)；
    上游 400 能力不支持→SKIPPED（模型版本差异，不计负分，§7.5-D 误报控制）。
  - B 组（单向确证）：支持→一票确证(PASS)；任何不支持→SKIPPED（不据此判套壳，§7.5-C）。

协议路径限制（§7.5-D）：OpenAI 兼容层（/v1/chat/completions）通常不透传 Gemini 专属
工具，整组 applicable=False（报告标注"兼容层无法做功能性指纹检测"）。高成本探针
（搜索接地/代码执行/视频/缓存）经预算计数器控量，单探针 unsupported 不独立定性、
须由评分层多探针加权（Task 18+）。

判定结果归入 authenticity 维度（strategy_category=authenticity, strategy_key=gemini_*），
经引擎汇总为真实性证据后由评分模型融入 direct_score / shell_score（设计 §9.5）。
"""
import json

from app.probes.base import Probe, ProbeCategory, ProbeContext, ProbeResult, ProbeStatus
from app.probes.registry import ProbeRegistry
from app.providers.base import AdapterRequest, ChatMessage
from app.utils.errors import ErrorCategory, ProbeError

_CATEGORY = ProbeCategory.AUTHENTICITY.value
_GEMINI = "gemini"
_COMPAT_MODE = "openai_compat"
# 多模态视为已声明能力的标识集合（与 capability 探针保持一致，DRY 语义）
_MULTIMODAL_CAPS = {"multimodal", "vision"}


def _is_capability_error(error: ProbeError) -> bool:
    """是否为"能力不支持"类错误（上游 400 参数/功能不支持）。"""
    return error.category is ErrorCategory.CAPABILITY


class GeminiFeatureProbe(Probe):
    """Gemini 功能性指纹探针基类：仅 Gemini 原生路径适用（兼容层整组跳过）。

    子类声明 key/name/group，并实现 `_build_request` 与 `_judge`；公共的适用性判定、
    预算守卫、错误归类由本基类模板方法统一处理（DRY + SOLID-O）。
    """

    category = _CATEGORY
    weight = 1.0
    group = "A"  # A=双向判真伪 / B=单向确证

    def applicable(self, ctx: ProbeContext) -> bool:
        # 仅 Gemini 原生路径有意义；兼容层不透传专属工具（§7.5-D）
        protocol = getattr(ctx.adapter, "protocol", None)
        return protocol == _GEMINI and ctx.access_mode != _COMPAT_MODE

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        guard = self._guard(ctx)
        if guard is not None:
            return guard
        request = self._build_request(ctx)
        try:
            response = await ctx.adapter.chat(request)
        except ProbeError as error:
            return self._on_error(error)
        finally:
            if ctx.budget is not None:
                ctx.budget.consume()
        return self._judge(ctx, response)

    def _guard(self, ctx: ProbeContext) -> ProbeResult | None:
        """前置守卫：不适用 / 预算耗尽时返回 SKIPPED，否则 None 放行。"""
        if not self.applicable(ctx):
            return self.skipped("仅 Gemini 原生路径适用（兼容层无法做功能性指纹检测）")
        if ctx.budget is not None and ctx.budget.exhausted:
            return self.skipped("预算耗尽")
        return None

    def _on_error(self, error: ProbeError) -> ProbeResult:
        """请求异常归类：能力不支持 SKIPPED（不计负分），其余按组语义处理。"""
        if _is_capability_error(error):
            return self.skipped("模型版本不支持该功能（上游 400），不计负分")
        # A 组：非能力类错误视为功能不可用（证伪）；B 组覆盖为不扣分
        return self.make_result(
            ProbeStatus.FAIL, score=0.0, evidence={"error_category": error.category.value}
        )

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        """构造触发目标功能的请求；子类实现具体 extra 参数。"""
        raise NotImplementedError

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        """据响应特有字段判定三态；子类实现。"""
        raise NotImplementedError


class _BGroupProbe(GeminiFeatureProbe):
    """B 组单向确证基类：支持即一票确证(PASS)，任何不支持一律不扣分(SKIPPED)。"""

    group = "B"

    def _on_error(self, error: ProbeError) -> ProbeResult:
        # B 组：不支持不能据此判套壳（可能仅另一平台直供或版本不含该功能，§7.5-C）
        return self.skipped(f"平台特有功能不可用（{error.category.value}），不据此判套壳")


# ============ A 组：通用 Gemini 独有功能探针（双向判真伪，§7.5-A） ============


@ProbeRegistry.register
class GeminiThinkingProbe(GeminiFeatureProbe):
    """思考模型：设 thinkingConfig 后应回 thoughtsTokenCount>0（思考预算消耗）。"""

    key = "gemini_thinking"
    name = "Gemini 思考"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="逐步推理：17 与 28 的乘积是多少？")],
            max_tokens=64,
            extra={"generationConfig": {"thinkingConfig": {"thinkingBudget": 128}}},
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        thoughts = response.feature_flags.get("thoughts_token_count")
        if thoughts is not None and thoughts > 0:
            return self.make_result(
                ProbeStatus.PASS, score=1.0, metrics={"thoughts_token_count": thoughts}
            )
        # 声称思考模型却无思考用量 → 证伪
        return self.make_result(
            ProbeStatus.FAIL,
            score=0.0,
            evidence={"reason": "thoughtsTokenCount 缺失或为 0"},
        )


@ProbeRegistry.register
class GeminiCodeExecutionProbe(GeminiFeatureProbe):
    """代码执行：声明 code_execution 工具后应回 executableCode + codeExecutionResult。"""

    key = "gemini_code_execution"
    name = "Gemini 代码执行"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="用 Python 计算 12 的阶乘并执行得到结果。")],
            max_tokens=128,
            extra={"tools": [{"code_execution": {}}]},
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        parts = response.feature_flags.get("code_execution") or []
        has_code = any("executableCode" in part for part in parts)
        has_result = any("codeExecutionResult" in part for part in parts)
        if has_code and has_result:
            return self.make_result(ProbeStatus.PASS, score=1.0)
        if has_code:
            # 只回代码文本、无执行结果 → 字段不全
            return self.make_result(
                ProbeStatus.DEGRADED, evidence={"reason": "仅 executableCode，缺执行结果"}
            )
        return self.make_result(
            ProbeStatus.FAIL, score=0.0, evidence={"reason": "无代码执行特有字段"}
        )


@ProbeRegistry.register
class GeminiSearchGroundingProbe(GeminiFeatureProbe):
    """搜索接地：声明 google_search 后应回 groundingMetadata.groundingChunks。"""

    key = "gemini_search_grounding"
    name = "Gemini 搜索接地"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="用搜索查一下今天有什么重要科技新闻？")],
            max_tokens=128,
            extra={"tools": [{"google_search": {}}]},
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        grounding = response.feature_flags.get("grounding_metadata") or {}
        chunks = grounding.get("groundingChunks") or []
        if chunks:
            return self.make_result(
                ProbeStatus.PASS, score=1.0, metrics={"grounding_chunks": len(chunks)}
            )
        if grounding:
            # 有 groundingMetadata 但无接地块 → 字段不全
            return self.make_result(
                ProbeStatus.DEGRADED, evidence={"reason": "groundingMetadata 无 groundingChunks"}
            )
        return self.make_result(
            ProbeStatus.FAIL, score=0.0, evidence={"reason": "无 groundingMetadata"}
        )


@ProbeRegistry.register
class GeminiJsonSchemaProbe(GeminiFeatureProbe):
    """受控结构化输出：responseMimeType=application/json + responseSchema 应严格遵循。"""

    key = "gemini_json_schema"
    name = "Gemini 受控结构化输出"
    _required_keys = ("city", "temperature")

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        schema = {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "temperature": {"type": "number"},
            },
            "required": list(self._required_keys),
            "propertyOrdering": list(self._required_keys),
        }
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="返回北京的城市名与气温。")],
            max_tokens=64,
            extra={
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": schema,
                }
            },
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        parsed = self._parse_object(response.content)
        if parsed is None:
            return self.make_result(
                ProbeStatus.FAIL, score=0.0, evidence={"reason": "输出非 JSON 对象，忽略 schema"}
            )
        missing = [key for key in self._required_keys if key not in parsed]
        if not missing:
            return self.make_result(ProbeStatus.PASS, score=1.0)
        return self.make_result(
            ProbeStatus.DEGRADED, evidence={"missing_keys": missing}
        )

    @staticmethod
    def _parse_object(content: str | None) -> dict | None:
        if not content:
            return None
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None


@ProbeRegistry.register
class GeminiCachingProbe(GeminiFeatureProbe):
    """上下文缓存：带 cachedContent 引用请求后应回 cachedContentTokenCount>0。"""

    key = "gemini_caching"
    name = "Gemini 上下文缓存"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        cached_ref = ctx.thresholds.get("cached_content_ref", "cachedContents/probe")
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="基于已缓存上下文回答：要点是什么？")],
            max_tokens=64,
            extra={"cachedContent": cached_ref},
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        cached = response.feature_flags.get("cached_content_token_count")
        if cached is not None and cached > 0:
            return self.make_result(
                ProbeStatus.PASS, score=1.0, metrics={"cached_content_token_count": cached}
            )
        return self.make_result(
            ProbeStatus.FAIL, score=0.0, evidence={"reason": "cachedContentTokenCount 缺失或为 0"}
        )


@ProbeRegistry.register
class GeminiLogprobsProbe(GeminiFeatureProbe):
    """logprobs：responseLogprobs=true 后应回 logprobsResult.{topCandidates,chosenCandidates}。"""

    key = "gemini_logprobs"
    name = "Gemini 对数概率"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="说一个词。")],
            max_tokens=8,
            extra={"generationConfig": {"responseLogprobs": True, "logprobs": 3}},
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        logprobs = response.feature_flags.get("logprobs_result") or {}
        has_chosen = bool(logprobs.get("chosenCandidates"))
        has_top = bool(logprobs.get("topCandidates"))
        if has_chosen and has_top:
            return self.make_result(ProbeStatus.PASS, score=1.0)
        if logprobs:
            return self.make_result(
                ProbeStatus.DEGRADED, evidence={"reason": "logprobsResult 字段不全"}
            )
        return self.make_result(
            ProbeStatus.FAIL, score=0.0, evidence={"reason": "无 logprobsResult"}
        )


@ProbeRegistry.register
class GeminiSafetyRatingsProbe(GeminiFeatureProbe):
    """安全评级：普通请求应回 safetyRatings[].{category,probability} 结构规范。"""

    key = "gemini_safety_ratings"
    name = "Gemini 安全评级"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="你好。")],
            max_tokens=8,
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        ratings = response.feature_flags.get("safety_ratings") or []
        well_formed = [
            item
            for item in ratings
            if isinstance(item, dict) and "category" in item and "probability" in item
        ]
        if well_formed:
            return self.make_result(
                ProbeStatus.PASS, score=1.0, metrics={"safety_rating_count": len(well_formed)}
            )
        if ratings:
            # 有 safetyRatings 但结构与 Gemini 不符 → 可疑
            return self.make_result(
                ProbeStatus.DEGRADED, evidence={"reason": "safetyRatings 结构不规范"}
            )
        return self.make_result(
            ProbeStatus.FAIL, score=0.0, evidence={"reason": "无 safetyRatings"}
        )


@ProbeRegistry.register
class GeminiTokenConsistencyProbe(GeminiFeatureProbe):
    """token 计数一致性：:countTokens 与 usageMetadata.promptTokenCount 应一致。"""

    key = "gemini_token_consistency"
    name = "Gemini Token 计数一致性"
    _pass_max = 0.1
    _degraded_max = 0.3

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        guard = self._guard(ctx)
        if guard is not None:
            return guard
        count_tokens = getattr(ctx.adapter, "count_tokens", None)
        if count_tokens is None:
            return self.skipped("适配器不支持 :countTokens")
        request = self._build_request(ctx)
        try:
            counted = await count_tokens(request)
            response = await ctx.adapter.chat(request)
        except ProbeError as error:
            return self._on_error(error)
        finally:
            if ctx.budget is not None:
                ctx.budget.consume(2)
        if counted is None:
            return self.skipped(":countTokens 端点不可用")
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else None
        if prompt_tokens is None:
            return self.make_result(
                ProbeStatus.FAIL, score=0.0, evidence={"reason": "usage.promptTokenCount 缺失"}
            )
        deviation = abs(counted - prompt_tokens) / max(counted, 1)
        metrics = {
            "counted": counted,
            "prompt_tokens": prompt_tokens,
            "deviation": round(deviation, 4),
        }
        if deviation <= self._pass_max:
            return self.make_result(ProbeStatus.PASS, score=1.0, metrics=metrics)
        if deviation <= self._degraded_max:
            return self.make_result(ProbeStatus.DEGRADED, metrics=metrics)
        return self.make_result(ProbeStatus.FAIL, score=0.0, metrics=metrics)

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="请逐字复述：人工智能正在重塑软件工程。")],
            max_tokens=16,
        )


@ProbeRegistry.register
class GeminiMultimodalTimestampProbe(GeminiFeatureProbe):
    """多模态时间戳：传视频 fileData 问"第 N 秒"应能定位（仅声明多模态的模型适用）。"""

    key = "gemini_multimodal_timestamp"
    name = "Gemini 多模态时间戳"

    def applicable(self, ctx: ProbeContext) -> bool:
        # 在原生 Gemini 基础上，还要求模型声明多模态，避免对纯文本模型误判负分
        return super().applicable(ctx) and bool(
            _MULTIMODAL_CAPS & ctx.declared_capabilities
        )

    def _guard(self, ctx: ProbeContext) -> ProbeResult | None:
        if not super().applicable(ctx):
            return self.skipped("仅 Gemini 原生路径适用（兼容层无法做功能性指纹检测）")
        if not (_MULTIMODAL_CAPS & ctx.declared_capabilities):
            return self.skipped("模型未声明多模态能力")
        if ctx.budget is not None and ctx.budget.exhausted:
            return self.skipped("预算耗尽")
        return None

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="这段视频第 3 秒发生了什么？")],
            max_tokens=64,
            extra={
                "fileData": {"mimeType": "video/mp4", "fileUri": "gs://probe/sample.mp4"}
            },
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        if response.success and response.content:
            return self.make_result(ProbeStatus.PASS, score=1.0)
        return self.make_result(
            ProbeStatus.FAIL, score=0.0, evidence={"reason": "多模态请求未返回有效内容"}
        )


# ============ B 组：平台特有功能探针（单向确证，§7.5-B） ============


@ProbeRegistry.register
class GeminiUrlContextProbe(_BGroupProbe):
    """URL Context（仅 Developer/Studio）：应回 url_context_metadata.urlMetadata。"""

    key = "gemini_url_context"
    name = "Gemini URL Context"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="总结 https://example.com 的内容。")],
            max_tokens=64,
            extra={"tools": [{"url_context": {}}]},
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        ucm = response.feature_flags.get("url_context_metadata") or {}
        url_meta = ucm.get("urlMetadata") or []
        retrieved = any(
            isinstance(item, dict)
            and item.get("retrievedUrl")
            and item.get("urlRetrievalStatus")
            for item in url_meta
        )
        if retrieved:
            # 一票确证：URL Context 为 Developer API 独有，套壳无法返回
            return self.make_result(
                ProbeStatus.PASS, score=1.0, metrics={"url_count": len(url_meta)}
            )
        return self.skipped("未返回 url_context_metadata，不据此判套壳")


@ProbeRegistry.register
class GeminiVertexRagProbe(_BGroupProbe):
    """Vertex RAG 检索接地（仅 Vertex）：接受 retrieval.vertexRagStore 并返回检索结果。"""

    key = "gemini_vertex_rag"
    name = "Gemini Vertex RAG"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        rag_store = ctx.thresholds.get(
            "vertex_rag_store", "projects/probe/locations/global/ragCorpora/0"
        )
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="基于知识库回答：要点是什么？")],
            max_tokens=64,
            extra={"tools": [{"retrieval": {"vertexRagStore": {"ragCorpus": rag_store}}}]},
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        grounding = response.feature_flags.get("grounding_metadata") or {}
        rag_chunks = response.feature_flags.get("retrieval_metadata") or grounding.get(
            "retrievalChunks"
        )
        if rag_chunks:
            return self.make_result(ProbeStatus.PASS, score=1.0)
        return self.skipped("未返回 Vertex RAG 检索结果，不据此判套壳")


@ProbeRegistry.register
class GeminiMapsGroundingProbe(_BGroupProbe):
    """Google Maps 接地（仅 Vertex Gemini 3 企业）：应回 Maps 接地元数据。"""

    key = "gemini_maps_grounding"
    name = "Gemini Maps 接地"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="附近有哪些咖啡馆？")],
            max_tokens=64,
            extra={"tools": [{"google_maps": {}}]},
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        maps_meta = response.feature_flags.get("maps_grounding_metadata")
        if maps_meta:
            return self.make_result(ProbeStatus.PASS, score=1.0)
        return self.skipped("未返回 Maps 接地元数据，不据此判套壳")


@ProbeRegistry.register
class GeminiSafetySeverityProbe(_BGroupProbe):
    """SafetySetting.method=SEVERITY（仅 Vertex）：应在 safetyRatings 回 severity 维度。"""

    key = "gemini_safety_severity"
    name = "Gemini 安全严重度方法"

    def _build_request(self, ctx: ProbeContext) -> AdapterRequest:
        return AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="你好。")],
            max_tokens=8,
            extra={
                "safetySettings": [
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_ONLY_HIGH",
                        "method": "SEVERITY",
                    }
                ]
            },
        )

    def _judge(self, ctx: ProbeContext, response) -> ProbeResult:
        ratings = response.feature_flags.get("safety_ratings") or []
        has_severity = any(
            isinstance(item, dict) and "severity" in item for item in ratings
        )
        if has_severity:
            # 一票确证：SEVERITY 方法为 Vertex 独有
            return self.make_result(ProbeStatus.PASS, score=1.0)
        return self.skipped("未生效 SEVERITY 安全方法，不据此判套壳")
