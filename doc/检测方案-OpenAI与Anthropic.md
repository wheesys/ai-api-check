# 检测方案：OpenAI / Anthropic

> 项目：中转站模型质量检测平台
> 关联设计：[可行性调研与方案设计.md](./可行性调研与方案设计.md) · [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md)
> 范围：OpenAI / Anthropic 两协议的检测全链路（基于 `backend/app` 实现归纳）
> 更新：2026-06-24

---

## 一、接入路径与适配器抽象

| 协议 | 接入形态 | 端点 | 关键归一化字段 |
|------|---------|------|--------------|
| **OpenAI** | `native` | `/v1/chat/completions` | `system_fingerprint`、`finish_reason`、`tool_calls`、`usage{prompt/completion/total}` |
| **Anthropic** | `native` | `/v1/messages` | `stop_reason`、`tool_use`、`usage{input/output_tokens}` |

- 所有探针只面向统一的 `ProviderAdapter`（`chat` / `stream_chat`）编程，**不感知协议收发细节**（SOLID-D）。
- 协议差异封装在各 adapter 内：
  - OpenAI 强制 `stream_options.include_usage=true`，确保流式末帧回传 usage，供计费核对；
  - Anthropic 流式默认不带 usage，由计费探针走本地估算兜底（不在适配器内补，关注点分离）。

---

## 二、五维检测矩阵

执行顺序：**连通性先行短路 → 其余类别串行、类别内受控并发 → 评分编排 → 三层落库**。

### 1. 连通性（致命短路）
最小请求（`max_tokens=1`）校验 HTTP 200 + 响应体结构合法。**失败即短路**，跳过后续全部探针并标记结果不可用。

### 2. 性能
| 探针 | 方法 | 判定 |
|------|------|------|
| TTFT | 流式取首个内容帧耗时，多次取**中位数** | <800ms PASS / <2500ms DEGRADED（lower-better） |
| 吞吐 | 流式统计 token/s（优先 usage，缺失退化帧计数） | >20tps PASS / >5tps DEGRADED（higher-better） |
| 稳定性 | 重复 N 次最小请求，成功率 + p95 延迟 | >0.99 PASS / >0.8 DEGRADED |

> 耗时测量用可注入时钟（默认 `time.perf_counter`），支持零网络确定性单测。

### 3. 计费一致性（key: `billing_consistency`）
固定提示词请求 → 上游申报 `prompt_tokens` 与本地 tokenizer 估算比对偏差率：
- 偏差 <15% PASS / <40% DEGRADED；
- **usage 缺失**（典型 Anthropic 流式）→ DEGRADED 并下调置信度，**不判 FAIL**；
- 配置单价时以 `Decimal` 精确核算成本（避免浮点误差）。

### 4. 能力（不支持 → `skipped` 不计负分）
流式 / 函数调用 / 受控 JSON / 多模态（仅声明时探测） / 上下文长度（二分逼近实测 vs 申报）。
上游返回 `400 capability_unsupported` 一律 skipped，避免误伤版本差异。
- 函数调用：读 `AdapterResponse.feature_flags["tool_calls"]`；有响应但未发起调用 → DEGRADED。
- 受控 JSON：响应内容可被 `json.loads` 解析为对象/数组即 PASS；否则 DEGRADED。

### 5. 真实性（核心，双子分取短板）

> **无罪推定**：从满分扣减，refute 信号减分、confirm 信号回补；最终 `authenticity = min(shell_score, direct_score)`。
> 本层为纯特征分析，不发起网络调用；仅给可信度分级，非铁证。

**套壳换底（shell_score）** — 判"是不是换了底座模型"：

| 信号 key | 权重 | OpenAI 触发 | Anthropic 触发 |
|---------|------|------------|---------------|
| `shell_usage_missing` | 30 | usage 完全缺失 / 字段不全 | 同左 |
| `shell_special_field_absent` | 25 | 缺 `system_fingerprint` | 缺 `stop_reason` |
| `shell_tokenizer_mismatch` | 20 | tokenizer 偏差≥40% HIT / ≥15% DEGRADED | 同左 |
| `shell_capability_gap` | 15 | 能力探针 fail 率≥50% HIT / ≥25% DEGRADED | 同左 |

**逆向/工具转出（direct_score）** — 判"是不是从 C 端工具逆向转出"：

| 信号 key | 权重 | OpenAI | Anthropic |
|---------|------|--------|-----------|
| `reverse_shell_artifact` | 25 | 正则命中 `<\|im_start\|>`、IDE 工具名(codex/cline/cursor/copilot/windsurf/antigravity)、注入人设/system prompt | 同左 |
| `reverse_version_anomaly` | 20 | 缺 `system_fingerprint` → HIT | **不适用**（仅 gemini/openai） |
| `reverse_ratelimit_pattern` | 20 | 限流贴近订阅档而非 API 配额 | 同左 |
| `reverse_header_missing` | 15 | 缺 `x-request-id`/`openai` 头 | 缺 `anthropic`/`request-id` 头 |

> 信号严重度三态 HIT / DEGRADED / MISS；命中仅记录匹配模式名，**脱敏不回显完整响应文本**。

---

## 三、评分聚合与分级

- **维度总分**：五维加权平均（默认权重各 1.0），状态归一 PASS=1 / DEGRADED=0.5 / FAIL=0，skipped 不计分母；连通性短路则总分标记不可用。
- **真实性维度**：直接采用双子分短板 `min(shell_score, direct_score)`。
- **真实性分级**（`ConfidenceGrader`）：阈值 **H=75 / L=45** 三级（正常 / 可能可疑 / 高度可疑），阈值可调。
- **置信度折扣**：兼容层接入（`openai_compat`）抹平原生指纹 → 依赖原生字段的信号 confidence **×0.6**，避免兼容层误判。

---

## 四、OpenAI vs Anthropic 关键差异速查

| 维度 | OpenAI | Anthropic |
|------|--------|-----------|
| 真伪特有字段 | `system_fingerprint` | `stop_reason` |
| 直供响应头 | `x-request-id` / `openai` | `anthropic` / `request-id` |
| 流式 usage | 强制 `include_usage` 回传 | 默认缺失 → 计费降级估算 |
| 版本指纹信号 | ✅ 参与 `reverse_version_anomaly` | ❌ 不适用 |

---

## 五、代码索引

| 关注点 | 文件 |
|--------|------|
| 适配器 | `backend/app/providers/openai_adapter.py`、`anthropic_adapter.py` |
| 连通性 / 性能 / 计费 / 能力探针 | `backend/app/probes/{connectivity,performance,billing,capability}.py` |
| 真实性信号提取 | `backend/app/probes/authenticity.py`、`signals.py` |
| 评分聚合 / 分级 | `backend/app/scoring/{aggregator,authenticity_scorer,confidence,orchestrator}.py` |
