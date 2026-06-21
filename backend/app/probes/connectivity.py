"""连通性探针（设计 §8.3）。

最小合法请求（1 token 输出），校验 HTTP 200 + 响应体结构合法。
连通性失败属致命短路信号（引擎据此跳过后续探针，见 §8.1）。
"""
from app.probes._common import PING_MESSAGES
from app.probes.base import Probe, ProbeCategory, ProbeContext, ProbeResult, ProbeStatus
from app.probes.registry import ProbeRegistry
from app.providers.base import AdapterRequest
from app.utils.errors import ProbeError


@ProbeRegistry.register
class ConnectivityProbe(Probe):
    """连通性探针：可达且响应结构合法即 PASS。"""

    key = "connectivity"
    category = ProbeCategory.CONNECTIVITY.value
    name = "连通性"
    weight = 1.0

    async def run(self, ctx: ProbeContext) -> ProbeResult:
        if ctx.budget is not None and ctx.budget.exhausted:
            return self.skipped("预算耗尽")
        request = AdapterRequest(
            model_name=ctx.model_name, messages=PING_MESSAGES, max_tokens=1
        )
        try:
            response = await ctx.adapter.chat(request)
        except ProbeError as error:
            return self.make_result(
                ProbeStatus.FAIL,
                score=0.0,
                metrics={"http_status": error.http_status},
                evidence={
                    "error_category": error.category.value,
                    "detail": error.message,
                },
            )
        finally:
            if ctx.budget is not None:
                ctx.budget.consume()
        if response.success and response.content is not None:
            return self.make_result(
                ProbeStatus.PASS,
                score=1.0,
                metrics={"http_status": response.http_status},
            )
        return self.make_result(
            ProbeStatus.FAIL,
            score=0.0,
            metrics={"http_status": response.http_status},
            evidence={"reason": "响应结构非法或为空"},
        )
