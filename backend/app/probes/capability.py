"""能力探测探针（设计 §8.5）。

对模型的流式、函数调用、多模态、受控 JSON、上下文长度五项能力做主动探测。
判定遵循设计约定：能力不支持（上游 400 capability_unsupported）一律 `skipped`、
不计负分，避免误伤版本差异（设计 §11 边界）。能力声明经
`ProbeContext.declared_capabilities` 裁剪适用性。

响应侧信号经适配器归一化字段读取（不在探针内拼协议细节，SOLID-D）：
- 函数调用：`AdapterResponse.feature_flags["tool_calls"]`
- 受控 JSON：`AdapterResponse.content` 可被 `json.loads` 解析为对象
"""
import json

from app.probes._common import PING_MESSAGES
from app.probes.base import Probe, ProbeCategory, ProbeContext, ProbeResult, ProbeStatus
from app.probes.registry import ProbeRegistry
from app.providers.base import AdapterRequest, ChatMessage
from app.utils.errors import ErrorCategory, ProbeError

_CATEGORY = ProbeCategory.CAPABILITY.value

# 函数调用探测：标准工具声明（协议无关，经适配器翻译为各家 tools/functionDeclarations）
SAMPLE_TOOL = {
    "name": "get_weather",
    "description": "查询指定城市的天气",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "城市名"}},
        "required": ["city"],
    },
}
FUNCTION_CALL_MESSAGES = [
    ChatMessage(role="user", content="北京现在天气怎么样？请调用工具查询。")
]
JSON_MODE_MESSAGES = [
    ChatMessage(
        role="user",
        content='只返回 JSON：{"city":"北京","ok":true}，不要任何额外文字。',
    )
]
# 多模态视为已声明能力的标识集合
_MULTIMODAL_CAPS = {"multimodal", "vision"}


def _is_capability_error(error: ProbeError) -> bool:
    """是否为"能力不支持"类错误（上游 400 参数/功能不支持）。"""
    return error.category is ErrorCategory.CAPABILITY


@ProbeRegistry.register
class CapStreamingProbe(Probe):
    """流式能力：能否经 SSE 逐帧产出内容。"""

    key = "cap_streaming"
    category = _CATEGORY
    name = "流式支持"
    weight = 1.0

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        if ctx.budget is not None and ctx.budget.exhausted:
            return self.skipped("预算耗尽")
        request = AdapterRequest(
            model_name=ctx.model_name, messages=PING_MESSAGES, max_tokens=8, stream=True
        )
        content_frames = 0
        try:
            async for chunk in ctx.adapter.stream_chat(request):
                if chunk.delta_text:
                    content_frames += 1
        except ProbeError as error:
            return self.make_result(
                ProbeStatus.FAIL,
                score=0.0,
                evidence={"error_category": error.category.value},
            )
        finally:
            if ctx.budget is not None:
                ctx.budget.consume()
        if content_frames > 0:
            return self.make_result(
                ProbeStatus.PASS, score=1.0, metrics={"content_frames": content_frames}
            )
        return self.make_result(
            ProbeStatus.FAIL, score=0.0, evidence={"reason": "未产出流式内容帧"}
        )


@ProbeRegistry.register
class CapFunctionCallProbe(Probe):
    """函数调用：携带工具声明后能否返回结构化调用。"""

    key = "cap_function_call"
    category = _CATEGORY
    name = "函数调用"
    weight = 1.0

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        if ctx.budget is not None and ctx.budget.exhausted:
            return self.skipped("预算耗尽")
        request = AdapterRequest(
            model_name=ctx.model_name,
            messages=FUNCTION_CALL_MESSAGES,
            max_tokens=64,
            extra={"tools": [SAMPLE_TOOL], "tool_choice": "auto"},
        )
        try:
            response = await ctx.adapter.chat(request)
        except ProbeError as error:
            if _is_capability_error(error):
                return self.skipped("模型不支持函数调用（上游 400）")
            return self.make_result(
                ProbeStatus.FAIL,
                score=0.0,
                evidence={"error_category": error.category.value},
            )
        finally:
            if ctx.budget is not None:
                ctx.budget.consume()
        tool_calls = response.feature_flags.get("tool_calls")
        if tool_calls:
            return self.make_result(
                ProbeStatus.PASS,
                score=1.0,
                metrics={"tool_call_count": len(tool_calls)},
            )
        # 有响应但未发起结构化调用：能力弱化而非完全缺失
        return self.make_result(
            ProbeStatus.DEGRADED, evidence={"reason": "未返回结构化工具调用"}
        )


@ProbeRegistry.register
class CapJsonModeProbe(Probe):
    """受控 JSON：能否输出可解析的结构化 JSON。"""

    key = "cap_json_mode"
    category = _CATEGORY
    name = "受控 JSON 输出"
    weight = 1.0

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        if ctx.budget is not None and ctx.budget.exhausted:
            return self.skipped("预算耗尽")
        request = AdapterRequest(
            model_name=ctx.model_name,
            messages=JSON_MODE_MESSAGES,
            max_tokens=64,
            extra={"response_format": {"type": "json_object"}},
        )
        try:
            response = await ctx.adapter.chat(request)
        except ProbeError as error:
            if _is_capability_error(error):
                return self.skipped("模型不支持受控 JSON（上游 400）")
            return self.make_result(
                ProbeStatus.FAIL,
                score=0.0,
                evidence={"error_category": error.category.value},
            )
        finally:
            if ctx.budget is not None:
                ctx.budget.consume()
        if self._is_valid_json_object(response.content):
            return self.make_result(ProbeStatus.PASS, score=1.0)
        # 有响应但非合法 JSON：受控输出未严格生效
        return self.make_result(
            ProbeStatus.DEGRADED, evidence={"reason": "输出非合法 JSON 对象"}
        )

    @staticmethod
    def _is_valid_json_object(content: str | None) -> bool:
        """内容是否可解析为 JSON 对象/数组。"""
        if not content:
            return False
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return False
        return isinstance(parsed, (dict, list))


@ProbeRegistry.register
class CapMultimodalProbe(Probe):
    """多模态：图像/音视频输入能否被接受。仅对声明多模态的模型适用。"""

    key = "cap_multimodal"
    category = _CATEGORY
    name = "多模态输入"
    weight = 1.0

    def applicable(self, ctx: ProbeContext) -> bool:
        # 未声明多模态能力则不适用，避免对纯文本模型误判负分
        return bool(_MULTIMODAL_CAPS & ctx.declared_capabilities)

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        if not self.applicable(ctx):
            return self.skipped("模型未声明多模态能力")
        if ctx.budget is not None and ctx.budget.exhausted:
            return self.skipped("预算耗尽")
        request = AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content="描述这张图片。")],
            max_tokens=32,
            extra={"image": {"type": "image_url", "url": "https://example.com/x.png"}},
        )
        try:
            response = await ctx.adapter.chat(request)
        except ProbeError as error:
            if _is_capability_error(error):
                return self.skipped("模型拒绝多模态输入（上游 400）")
            return self.make_result(
                ProbeStatus.FAIL,
                score=0.0,
                evidence={"error_category": error.category.value},
            )
        finally:
            if ctx.budget is not None:
                ctx.budget.consume()
        if response.success and response.content:
            return self.make_result(ProbeStatus.PASS, score=1.0)
        return self.make_result(
            ProbeStatus.FAIL, score=0.0, evidence={"reason": "多模态请求未返回有效内容"}
        )


@ProbeRegistry.register
class CapContextLengthProbe(Probe):
    """上下文长度：二分逼近实测可用上下文，与申报值比对（设步长/硬上限控消耗）。"""

    key = "cap_context_length"
    category = _CATEGORY
    name = "上下文长度"
    weight = 1.0

    def __init__(self, padder=None) -> None:
        # 填充器：单位数 → 对应规模的填充文本，可注入以适配不同 tokenizer 与测试
        self._padder = padder or (lambda units: "词" * units)

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        declared = ctx.thresholds.get("declared_context")
        if not declared:
            return self.skipped("未提供申报上下文长度")
        max_iters = int(ctx.thresholds.get("context_max_iters", 8))
        low, high = 1, int(declared)
        measured = 0
        probes_used = 0
        while low <= high and probes_used < max_iters:
            if ctx.cancelled() or (ctx.budget is not None and ctx.budget.exhausted):
                break
            mid = (low + high) // 2
            probes_used += 1
            accepted = await self._try_size(ctx, mid)
            if ctx.budget is not None:
                ctx.budget.consume()
            if accepted:
                measured = mid
                low = mid + 1
            else:
                high = mid - 1
        if probes_used == 0:
            return self.skipped("预算耗尽或已取消")
        ratio = measured / int(declared)
        # 实测/申报比越接近 1 越好
        if ratio >= 0.9:
            status = ProbeStatus.PASS
        elif ratio >= 0.5:
            status = ProbeStatus.DEGRADED
        else:
            status = ProbeStatus.FAIL
        return self.make_result(
            status,
            metrics={
                "measured_context": measured,
                "declared_context": int(declared),
                "ratio": round(ratio, 3),
                "probes_used": probes_used,
            },
        )

    async def _try_size(self, ctx: ProbeContext, units: int) -> bool:
        """以指定规模输入试探一次；成功返回 True，能力/参数错误返回 False。"""
        request = AdapterRequest(
            model_name=ctx.model_name,
            messages=[ChatMessage(role="user", content=self._padder(units))],
            max_tokens=1,
        )
        try:
            response = await ctx.adapter.chat(request)
        except ProbeError:
            return False
        return response.success
