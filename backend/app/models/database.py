"""SQLAlchemy ORM 模型：三层结果数据模型（设计 v1.3 第 6 节）。

遵循全局 DB 规则 3.2：
- 主键：整数自增（`Integer primary_key autoincrement`），禁止字符串主键；
- 外键：不使用数据库级外键约束，表间关联以普通整数列（如 `station_id`/`task_id`/
  `strategy_result_id`）承载，关联完整性由应用层维护；
- 时间：统一 `DateTime`（禁止 `date`），默认值落 UTC；
- 价格：`input_price`/`output_price` 用 `Text` 存精确小数，应用层 Decimal 计算
  （SQLite 无原生 DECIMAL）；
- 注释：每列以 `comment=` 落地字段含义（SQLite 不生成列 COMMENT，但元数据保留，
  并同步维护 `doc/DATABASE_SCHEMA.md` 数据字典）。
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """返回带时区的当前 UTC 时间，避免 datetime.utcnow() 弃用告警。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """全部 ORM 模型的声明式基类。"""


class RelayStation(Base):
    """① 中转站：连接信息与协议集合。"""

    __tablename__ = "relay_stations"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="主键，整数自增"
    )
    name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="中转站显示名称"
    )
    protocols: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="协议集合 JSON 数组文本，元素∈openai/anthropic/gemini；兼容站为多元素",
    )
    base_url: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="中转站 API 基础地址"
    )
    api_key_encrypted: Mapped[str] = mapped_column(
        Text, nullable=False, comment="API Key 密文（Fernet 加密，禁止明文落库/外发/打印）"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        comment="站点状态：active 启用 / disabled 停用",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, comment="创建时间(UTC)"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        comment="更新时间(UTC)",
    )


class Model(Base):
    """② 模型：归属某站点的单一协议模型，含 Gemini 原生/兼容层接入配置。"""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="主键，整数自增"
    )
    station_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="业务关联：所属中转站 id（无外键约束）"
    )
    protocol: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="模型归属协议：openai/anthropic/gemini，须∈所属站 protocols",
    )
    access_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="native",
        comment="接入形态：native 原生协议 / openai_compat 兼容层转出；非 Gemini 默认 native",
    )
    gemini_endpoint_style: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="Gemini 原生端点形态：gemini_developer/vertex；仅 gemini+native 有意义",
    )
    gemini_vertex_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Vertex 专属配置 JSON：{project,location,auth_style}；非 Vertex 留空",
    )
    model_name: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="模型标识名（调用 API 时使用）"
    )
    display_name: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="模型展示名（报告/界面显示）"
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="manual",
        comment="来源：fetched 自动拉取 / manual 手工录入",
    )
    input_price: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="输入单价（TEXT 精确小数，应用层 Decimal 计算）"
    )
    output_price: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="输出单价（TEXT 精确小数，应用层 Decimal 计算）"
    )
    declared_context_length: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="声明的上下文长度（token）"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否启用该模型用于检测"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, comment="创建时间(UTC)"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        comment="更新时间(UTC)",
    )


class DetectionTask(Base):
    """③ 检测任务：一次（站点,模型）检测的生命周期与配置快照。"""

    __tablename__ = "detection_tasks"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="主键，整数自增"
    )
    station_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="业务关联：目标中转站 id"
    )
    model_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="业务关联：目标模型 id"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="状态：pending/running/completed/failed/canceled",
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="进度百分比 0-100"
    )
    config_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="检测配置快照 JSON（价格/上下文/探针开关等，保证历史报告可复现）",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="开始执行时间(UTC)"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="终态时间(UTC)"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="任务级错误信息（脱敏，禁含 Key）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, comment="创建时间(UTC)"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        comment="更新时间(UTC)",
    )


class DetectionResult(Base):
    """④ 结果汇总：模型综合评分（三层结果模型顶层）。"""

    __tablename__ = "detection_results"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="主键，整数自增"
    )
    task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="业务关联：所属检测任务 id"
    )
    model_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="业务关联：被检测模型 id"
    )
    overall_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="综合评分（五维加权合成）"
    )
    connectivity_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="连通性维度得分"
    )
    performance_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="性能维度得分"
    )
    billing_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="计费一致性维度得分"
    )
    capability_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="能力维度得分"
    )
    authenticity_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="真实性维度得分（套壳分与逆向转出分取短板）"
    )
    authenticity_subscores_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="真实性子分明细 JSON：shell_score/direct_score/confidence + 逐条信号",
    )
    details_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="各维度评分计算明细与聚合上下文快照 JSON（真实性信号置于上一列）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, comment="创建时间(UTC)"
    )


class StrategyResult(Base):
    """⑤ 策略检测结果：每模型×每检测策略一行（三层结果模型中层）。"""

    __tablename__ = "strategy_results"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="主键，整数自增"
    )
    task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="业务关联：所属检测任务 id"
    )
    model_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="业务关联：被检测模型 id"
    )
    strategy_category: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="策略大类：connectivity/performance/billing/capability/authenticity",
    )
    strategy_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="策略细项标识，如 ttft/throughput/billing_consistency/gemini_thinking",
    )
    strategy_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="策略中文名（报告展示）"
    )
    result_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="判定状态：pass/fail/degraded/skipped（功能探针 supported/unsupported 映射于此）",
    )
    score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="该策略得分（可空）"
    )
    weight: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="该策略在所属维度的权重快照"
    )
    metrics_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="量化指标 JSON，如 {ttft_ms:..} 或命中的特有字段名"
    )
    evidence_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="判定证据 JSON（脱敏：命中/缺失字段、原始片段引用）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, comment="创建时间(UTC)"
    )


class ProbeRecord(Base):
    """⑥ 探针原始记录：策略下每次 HTTP 往返一行（三层结果模型底层，可溯源）。"""

    __tablename__ = "probe_records"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="主键，整数自增"
    )
    task_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="业务关联：所属检测任务 id"
    )
    strategy_result_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
        comment="业务关联：归属哪条策略结果 id（无外键约束）",
    )
    probe_type: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="探针类型标识"
    )
    request_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="请求发出时间(UTC)"
    )
    response_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="响应完成时间(UTC)"
    )
    ttft_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="首 token 时延（毫秒）"
    )
    http_status: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="HTTP 响应状态码"
    )
    success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="该次请求是否成功"
    )
    usage_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="上游申报用量 JSON（prompt/completion/total tokens 等）"
    )
    estimated_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="本地估算 token 数（计费一致性比对用）"
    )
    feature_flags_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="功能特征标记 JSON（功能性指纹探针命中项）"
    )
    raw_response_json: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="原始响应片段 JSON（脱敏，禁含 Key/敏感头）"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="该次请求错误信息（脱敏）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utcnow, comment="创建时间(UTC)"
    )
