"""Pydantic v2 DTO：API 层请求/响应模型（与 ORM 解耦）。

安全红线（全局规则 3.5）：响应模型严禁回显 api_key；创建模型接收的明文 Key
仅用于服务层加密落库，不在任何响应/日志中出现。

说明：字段含 `model_name`/`model_id`，与 Pydantic v2 受保护命名空间 `model_` 冲突，
故统一以 `_Schema` 基类禁用该保护（protected_namespaces=()），避免运行期告警。
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 协议与状态取值集中定义，避免散落硬编码（DRY）
Protocol = Literal["openai", "anthropic", "gemini"]
AccessMode = Literal["native", "openai_compat"]
GeminiEndpointStyle = Literal["gemini_developer", "vertex"]
ModelSource = Literal["fetched", "manual"]
TaskStatus = Literal["pending", "running", "completed", "failed", "canceled"]
StrategyCategory = Literal[
    "connectivity", "performance", "billing", "capability", "authenticity"
]
ResultStatus = Literal["pass", "fail", "degraded", "skipped"]


class _Schema(BaseModel):
    """全部 DTO 的基类：禁用 model_ 受保护命名空间，避免字段名冲突告警。"""

    model_config = ConfigDict(protected_namespaces=())


class _ORMSchema(_Schema):
    """只读响应基类：开启 from_attributes 以便从 ORM 实例直接构造。"""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


# ---------- 中转站 ----------
class RelayStationBase(_Schema):
    """中转站公共字段。"""

    name: str = Field(..., min_length=1, max_length=128, description="中转站显示名称")
    protocols: list[Protocol] = Field(
        ..., min_length=1, description="协议集合，至少一个；兼容站为多元素"
    )
    base_url: str = Field(..., min_length=1, max_length=512, description="API 基础地址")
    status: Literal["active", "disabled"] = Field("active", description="站点状态")

    @field_validator("protocols")
    @classmethod
    def dedupe_protocols(cls, value: list[str]) -> list[str]:
        """去重协议集合，保持稳定顺序。"""
        seen: list[str] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        return seen


class RelayStationCreate(RelayStationBase):
    """创建中转站：携带明文 api_key（仅服务层加密用，不回显）。"""

    api_key: str = Field(..., min_length=1, description="API Key 明文，加密后落库")


class RelayStationUpdate(_Schema):
    """更新中转站：全部可选；api_key 为 None 表示不修改。"""

    name: str | None = Field(None, min_length=1, max_length=128)
    protocols: list[Protocol] | None = Field(None, min_length=1)
    base_url: str | None = Field(None, min_length=1, max_length=512)
    api_key: str | None = Field(None, min_length=1, description="如提供则更新并加密")
    status: Literal["active", "disabled"] | None = None


class RelayStationResponse(_ORMSchema):
    """中转站响应：严禁包含 api_key。"""

    id: int
    name: str
    protocols: list[Protocol]
    base_url: str
    status: str
    created_at: datetime
    updated_at: datetime


# ---------- 模型 ----------
class ModelBase(_Schema):
    """模型公共字段。"""

    protocol: Protocol = Field(..., description="模型归属协议")
    access_mode: AccessMode = Field("native", description="接入形态")
    gemini_endpoint_style: GeminiEndpointStyle | None = Field(
        None, description="Gemini 原生端点形态；仅 gemini+native 有意义"
    )
    gemini_vertex_json: dict | None = Field(
        None, description="Vertex 专属配置 {project,location,auth_style}"
    )
    model_name: str = Field(..., min_length=1, max_length=256, description="模型标识名")
    display_name: str | None = Field(None, max_length=256, description="模型展示名")
    source: ModelSource = Field("manual", description="来源")
    input_price: Decimal | None = Field(None, ge=0, description="输入单价")
    output_price: Decimal | None = Field(None, ge=0, description="输出单价")
    declared_context_length: int | None = Field(
        None, ge=0, description="声明上下文长度"
    )
    enabled: bool = Field(True, description="是否启用")


class ModelCreate(ModelBase):
    """创建模型：归属某中转站。"""

    station_id: int = Field(..., description="所属中转站 id")


class ModelResponse(_ORMSchema, ModelBase):
    """模型响应。"""

    id: int
    station_id: int
    created_at: datetime
    updated_at: datetime


# ---------- 检测任务 ----------
class DetectionTaskCreate(_Schema):
    """创建检测任务。"""

    station_id: int = Field(..., description="目标中转站 id")
    model_id: int = Field(..., description="目标模型 id")
    config: dict | None = Field(
        None, description="检测配置（探针开关/价格覆盖等），落为 config_json 快照"
    )


class DetectionTaskResponse(_ORMSchema):
    """检测任务响应。"""

    id: int
    station_id: int
    model_id: int
    status: TaskStatus
    progress: int
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


# ---------- 结果（三层，只读响应） ----------
class DetectionResultResponse(_ORMSchema):
    """结果汇总响应。"""

    id: int
    task_id: int
    model_id: int
    overall_score: float | None
    connectivity_score: float | None
    performance_score: float | None
    billing_score: float | None
    capability_score: float | None
    authenticity_score: float | None
    authenticity_subscores_json: str | None
    details_json: str | None
    created_at: datetime


class StrategyResultResponse(_ORMSchema):
    """策略检测结果响应。"""

    id: int
    task_id: int
    model_id: int
    strategy_category: StrategyCategory
    strategy_key: str
    strategy_name: str
    result_status: ResultStatus
    score: float | None
    weight: float | None
    metrics_json: str | None
    evidence_json: str | None
    created_at: datetime


class ProbeRecordResponse(_ORMSchema):
    """探针原始记录响应。"""

    id: int
    task_id: int
    strategy_result_id: int
    probe_type: str
    request_at: datetime | None
    response_at: datetime | None
    ttft_ms: int | None
    http_status: int | None
    success: bool
    usage_json: str | None
    estimated_tokens: int | None
    feature_flags_json: str | None
    raw_response_json: str | None
    error_message: str | None
    created_at: datetime
