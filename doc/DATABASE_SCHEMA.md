# 数据库数据字典

> 项目：中转站模型质量检测平台
> 关联设计：[可行性调研与方案设计.md](./可行性调研与方案设计.md) 第 6 节
> ORM 定义：`backend/app/models/database.py`

## 设计约束（全局 DB 规则 3.2 落地）

| 规则 | 落地方式 |
|---|---|
| 主键非字符串 | 全表 `id` 为 `INTEGER PRIMARY KEY AUTOINCREMENT` |
| 禁用外键 | 表间以普通整数列（`station_id`/`task_id`/`model_id`/`strategy_result_id`）业务关联，无 `FOREIGN KEY` 约束，完整性由应用层维护 |
| 禁用 date | 时间统一 `DATETIME`（存 UTC），无 `DATE` 类型 |
| 全字段注释 | SQLite 不支持列级 `COMMENT`，落地为 ORM `comment=` 元数据 + 本数据字典；迁移至 PG/MySQL 时改用原生 `COMMENT` |
| 价格精确 | `input_price`/`output_price` 用 `TEXT` 存精确小数，应用层 `Decimal` 计算（SQLite 无原生 DECIMAL） |

三层结果模型：`detection_results`（模型综合分）→ `strategy_results`（每模型每策略结论）→ `probe_records`（策略下每次请求原始记录），自顶向下可下钻溯源。

---

## ① relay_stations（中转站）

| 列 | 类型 | 空 | 默认 | 说明 |
|---|---|---|---|---|
| id | INTEGER | 否 | 自增 | 主键 |
| name | VARCHAR(128) | 否 | | 中转站显示名称 |
| protocols | TEXT | 否 | | 协议集合 JSON 数组，元素∈openai/anthropic/gemini；兼容站为多元素 |
| base_url | VARCHAR(512) | 否 | | API 基础地址 |
| api_key_encrypted | TEXT | 否 | | API Key 密文（Fernet 加密，禁止明文落库/外发/打印） |
| status | VARCHAR(32) | 否 | active | 站点状态：active/disabled |
| created_at | DATETIME | 否 | UTC now | 创建时间 |
| updated_at | DATETIME | 否 | UTC now | 更新时间（onupdate） |

## ② models（模型）

| 列 | 类型 | 空 | 默认 | 说明 |
|---|---|---|---|---|
| id | INTEGER | 否 | 自增 | 主键 |
| station_id | INTEGER | 否 | | 业务关联：所属中转站 id（索引，无外键） |
| protocol | VARCHAR(32) | 否 | | 模型归属协议：openai/anthropic/gemini，须∈所属站 protocols |
| access_mode | VARCHAR(32) | 否 | native | 接入形态：native 原生 / openai_compat 兼容层 |
| gemini_endpoint_style | VARCHAR(32) | 是 | | Gemini 原生端点形态：gemini_developer/vertex；仅 gemini+native 有意义 |
| gemini_vertex_json | TEXT | 是 | | Vertex 专属配置 JSON：{project,location,auth_style} |
| model_name | VARCHAR(256) | 否 | | 模型标识名（调用 API 用） |
| display_name | VARCHAR(256) | 是 | | 模型展示名 |
| source | VARCHAR(32) | 否 | manual | 来源：fetched 自动拉取 / manual 手输 |
| input_price | TEXT | 是 | | 输入单价（精确小数，Decimal 计算） |
| output_price | TEXT | 是 | | 输出单价（精确小数，Decimal 计算） |
| declared_context_length | INTEGER | 是 | | 声明上下文长度（token） |
| enabled | BOOLEAN | 否 | 1 | 是否启用 |
| created_at | DATETIME | 否 | UTC now | 创建时间 |
| updated_at | DATETIME | 否 | UTC now | 更新时间（onupdate） |

## ③ detection_tasks（检测任务）

| 列 | 类型 | 空 | 默认 | 说明 |
|---|---|---|---|---|
| id | INTEGER | 否 | 自增 | 主键 |
| station_id | INTEGER | 否 | | 业务关联：目标中转站 id（索引） |
| model_id | INTEGER | 否 | | 业务关联：目标模型 id（索引） |
| status | VARCHAR(32) | 否 | pending | pending/running/completed/failed/canceled |
| progress | INTEGER | 否 | 0 | 进度百分比 0-100 |
| config_json | TEXT | 是 | | 检测配置快照 JSON（保证历史报告可复现） |
| started_at | DATETIME | 是 | | 开始执行时间 |
| finished_at | DATETIME | 是 | | 终态时间 |
| error_message | TEXT | 是 | | 任务级错误（脱敏，禁含 Key） |
| created_at | DATETIME | 否 | UTC now | 创建时间 |
| updated_at | DATETIME | 否 | UTC now | 更新时间（onupdate） |

## ④ detection_results（结果汇总）

| 列 | 类型 | 空 | 默认 | 说明 |
|---|---|---|---|---|
| id | INTEGER | 否 | 自增 | 主键 |
| task_id | INTEGER | 否 | | 业务关联：所属任务 id（索引） |
| model_id | INTEGER | 否 | | 业务关联：被检测模型 id（索引） |
| overall_score | FLOAT | 是 | | 综合评分（五维加权） |
| connectivity_score | FLOAT | 是 | | 连通性维度得分 |
| performance_score | FLOAT | 是 | | 性能维度得分 |
| billing_score | FLOAT | 是 | | 计费一致性维度得分 |
| capability_score | FLOAT | 是 | | 能力维度得分 |
| authenticity_score | FLOAT | 是 | | 真实性维度得分（套壳分与逆向转出分取短板） |
| authenticity_subscores_json | TEXT | 是 | | 真实性子分 JSON：shell_score/direct_score/confidence + 逐条信号 |
| details_json | TEXT | 是 | | 各维度评分计算明细与聚合上下文快照 JSON |
| created_at | DATETIME | 否 | UTC now | 创建时间 |

## ⑤ strategy_results（策略检测结果，每模型×每策略一行）

| 列 | 类型 | 空 | 默认 | 说明 |
|---|---|---|---|---|
| id | INTEGER | 否 | 自增 | 主键 |
| task_id | INTEGER | 否 | | 业务关联：所属任务 id（索引） |
| model_id | INTEGER | 否 | | 业务关联：被检测模型 id（索引） |
| strategy_category | VARCHAR(32) | 否 | | connectivity/performance/billing/capability/authenticity |
| strategy_key | VARCHAR(64) | 否 | | 策略细项，如 ttft/throughput/billing_consistency/gemini_thinking |
| strategy_name | VARCHAR(128) | 否 | | 策略中文名 |
| result_status | VARCHAR(32) | 否 | | pass/fail/degraded/skipped |
| score | FLOAT | 是 | | 该策略得分 |
| weight | FLOAT | 是 | | 该策略在所属维度的权重快照 |
| metrics_json | TEXT | 是 | | 量化指标 JSON（如 ttft_ms 等） |
| evidence_json | TEXT | 是 | | 判定证据 JSON（脱敏） |
| created_at | DATETIME | 否 | UTC now | 创建时间 |

## ⑥ probe_records（探针原始记录，每次 HTTP 往返一行）

| 列 | 类型 | 空 | 默认 | 说明 |
|---|---|---|---|---|
| id | INTEGER | 否 | 自增 | 主键 |
| task_id | INTEGER | 否 | | 业务关联：所属任务 id（索引） |
| strategy_result_id | INTEGER | 否 | | 业务关联：归属策略结果 id（索引，无外键） |
| probe_type | VARCHAR(64) | 否 | | 探针类型标识 |
| request_at | DATETIME | 是 | | 请求发出时间 |
| response_at | DATETIME | 是 | | 响应完成时间 |
| ttft_ms | INTEGER | 是 | | 首 token 时延（毫秒） |
| http_status | INTEGER | 是 | | HTTP 状态码 |
| success | BOOLEAN | 否 | 0 | 该次请求是否成功 |
| usage_json | TEXT | 是 | | 上游申报用量 JSON |
| estimated_tokens | INTEGER | 是 | | 本地估算 token 数 |
| feature_flags_json | TEXT | 是 | | 功能特征标记 JSON（功能性指纹命中项） |
| raw_response_json | TEXT | 是 | | 原始响应片段 JSON（脱敏，禁含 Key/敏感头） |
| error_message | TEXT | 是 | | 该次请求错误（脱敏） |
| created_at | DATETIME | 否 | UTC now | 创建时间 |
