# 待办事项（TODO）

> 项目：中转站模型质量检测平台
> 关联设计：[可行性调研与方案设计.md](./可行性调研与方案设计.md)
> 规则：每项工作完成后在此标记 `[x]`；进行中标记 `[~]`

**图例**：`[x]` 已完成 · `[~]` 进行中 · `[ ]` 未开始

---

## 阶段一：调研与方案设计

- [x] 读取项目文档、探索项目上下文
- [x] 需求澄清（部署形态 / 检测维度 / 真实性基线 / 技术栈 / 前端框架）
- [x] 可行性分析
- [x] 整体架构与关键技术决策
- [x] **纳入 Gemini 协议**（协议集合多选建模 + 原生/兼容层双路径 + 逆向特征，见设计 v0.2 第 7 节）
- [x] 数据模型设计（含 `protocols`/`protocol`/`access_mode` + `strategy_result` 三层结果模型，已评审，见设计 v0.6）
- [x] 检测引擎与探针集详细设计（引擎执行模型 + 探针统一抽象 + 各类探针判定逻辑，见设计 v0.7 第 8 节）
- [x] 真实性评分模型详细设计（双子分取短板 + 信号加权 + Gemini 功能性指纹融入，见设计 v0.8 第 9 节）
- [x] 核心数据流与 API 设计（端到端流程 + REST/SSE 接口契约 + 安全约束，见设计 v0.9 第 10 节）
- [x] 错误处理与边界设计（统一错误分类 + 重试退避 + 探针级容错 + 边界条件，见设计 v1.0 第 11 节）
- [x] 报告与 PDF 设计（九区块报告结构 + ECharts 图表 + weasyprint 导出流程 + 红线脱敏，见设计 v1.1 第 12 节）
- [x] 测试策略（三层测试 + 评分模型核心测试 + 适配器双路径 + 零网络夹具/Key 脱敏回归，见设计 v1.2 第 13 节）
- [x] 设计文档完整评审（自检评审输出 7 项发现 → v1.3 全部修订闭环；用户评审通过）

---

## 阶段二：实施规划

- [x] 生成详细实施计划（writing-plans，见 [docs/superpowers/plans/2026-06-20-implementation-plan.md](../docs/superpowers/plans/2026-06-20-implementation-plan.md)，38 任务 127 步）
- [ ] 确认 Docker 时区需求（若使用 docker-compose，延后至 Task 38）

---

## 阶段三：工程搭建

- [~] 项目脚手架（后端 FastAPI / 前端 Vue3 + Naive UI / SQLite）—— **后端基础设施完成（Task 1-5）**：venv+依赖、config/main(lifespan)、ORM、安全、适配器框架、HTTP 客户端；前端待 Phase 6
- [x] 数据库 schema 与迁移（遵循 DB 规则 3.2）—— **Task 2 已完成**：六表 ORM（整数主键/无外键/datetime/价格 TEXT/全字段注释）+ Pydantic DTO + lifespan 建表 + 数据字典 [DATABASE_SCHEMA.md](./DATABASE_SCHEMA.md)，7 项集成测试通过
- [x] API Key 加密存储 + 日志脱敏（安全规则 3.5）—— **Task 3 已完成**：Fernet `KeyManager`（主密钥不入库/不回显）+ `ErrorSanitizer`（Key/token/Authorization 正则脱敏 + 敏感头剔除），10 项单测通过
- [x] Provider 适配器框架 + 统一错误模型 + 异步 HTTP 客户端 —— **Task 4-5 已完成**：`ProviderAdapter` 抽象 + `AdapterFactory` 注册表（SOLID-O）、九类 `ErrorCategory`/`ProbeError`、aiohttp+tenacity 退避重试客户端，共 11 项单测通过
- [ ] API Key 加密存储 + 日志脱敏（安全规则 3.5）
- [ ] README（含许可证展示格式 `许可证名称 © 年份 作者`）

---

## 阶段四：核心功能实现

- [x] 中转站 CRUD（协议集合多选 + 自定义地址/Key/名称）—— **Task 21 已完成**：`StationService`（Fernet 加密落库、响应经 to_response 仅暴露 has_api_key 不回显 Key/密文、protocols JSON 序列化）+ 中转站 CRUD 五端点
- [x] 模型列表自动拉取（OpenAI/Anthropic/Gemini 原生 + Gemini 兼容层；失败回退手输）—— **Task 21 已完成**：`ModelService` 多适配器并行拉取、(protocol,model_name,access_mode) 三元组去重（含库内去重）、部分失败逐项脱敏记录、全失败回退手输；模型列表/手动录入/拉取编排端点（Gemini 原生 Developer + 兼容层双路径），共 14 项集成测试通过
- [x] Provider 适配器（OpenAI / Anthropic / **Gemini 原生+兼容层双路径** / 兼容）—— **Task 6-8 已完成**：OpenAIAdapter、AnthropicAdapter、GeminiNativeAdapter（Developer/Vertex 双风格端点路由 + `:countTokens` + 功能性指纹字段提取）、GeminiOpenAICompatAdapter（复用 OpenAI 解析）；包导入即自注册四组合，共 47 项单测通过
- [x] 检测引擎：异步任务池 + SSE 进度 —— **Task 15-16,22 已完成**：执行器（连通性短路/类别内受控并发/SSE 事件/取消/超时/单点隔离）+ 全局调度器（任务间并发排队）+ `TaskService`（创建校验 model∈station、构建适配器/上下文、跑执行器、`ScoringOrchestrator` 评分编排、三层结果落库 detection_task 终态/strategy_result 中层/detection_result 汇总）+ `EventBroker` per-task SSE 通道 + 任务 API（创建异步触发/列表/详情/取消/SSE 事件流），共 11+9+5 项测试通过
- [x] 探针边界条件处理（设计 §11.5）—— **Task 14 已完成**：复用既有九类错误模型（`app/utils/errors.py`，不重复定义），新增 `app/probes/boundaries.py`——finish_reason 跨协议正误截断归一（stop/length 正常不计失败、safety/refusal 异常计失败）、流式中断检测（`collect_stream`：部分帧后断连标 incomplete 降级、零帧失败上抛交短路）、空响应判定，共 10 项单测通过；usage 缺失兜底已就地于计费探针处理不重复
- [x] 全局任务调度器（设计 §8.1 任务间并发）—— **Task 16 已完成**：`Scheduler` 以全局信号量限并发任务数（超额排队）、QUEUED→RUNNING→DONE/FAILED/CANCELED 状态流转、submit 工厂延迟创建协程、join/cancel/wait_all、重复提交拒绝，共 9 项集成测试通过；任务内探针并发由执行器封装（关注点分离）
- [~] 探针集：连通性 / 性能(TTFT/吞吐) / 计费一致性 / 能力探测（含 Gemini `usageMetadata`/`:countTokens` 解析）—— **Task 9-11 已完成**：探针抽象框架（Probe/ProbeContext/ProbeResult/ProbeStatus + ProbeRegistry 按类分组）、连通性探针、TTFT/吞吐/稳定性性能探针（可注入时钟确定性测耗时）、计费一致性探针（本地 tokenizer 估算 vs 申报 usage 偏差三态 + Decimal 成本核算）、五项能力探针（流式/函数调用/受控 JSON/多模态/上下文长度二分逼近，能力不支持 skipped 不计负分），共 48 项单测通过；真实性探针待 Task 12
- [x] 真实性探针：套壳换底特征 + 逆向/工具转出特征（含 Gemini CLI/AI Studio/Antigravity 逆向特征）—— **Task 12 已完成**：Signal 信号模型（target=shell/direct + direction=confirm/refute + severity 三态 + confidence）、AuthenticityEvidence 证据包、AuthenticitySignalExtractor 独立注册表；四项套壳信号（usage 缺失/特有字段缺失/分词不符/能力大面积失败）+ 五项逆向信号（工具壳痕迹/版本指纹异常/限流模式/直供头缺失/AI Studio 逆向），兼容层置信度自动 ×0.6，skipped 不计负分，共 24 项单测通过；评分聚合待 Task 18-19
- [x] **Gemini 功能性指纹探针**：搜索接地/URL Context/代码执行/思考/受控输出/缓存/logprobs/Vertex RAG 等偏门功能主动探测 + Studio/Vertex 来源定位（见设计 v0.3 第 7.5 节，仅 native 路径）—— **Task 13 已完成**：`GeminiFeatureProbe` 基类（模板方法统一适用性/预算/错误归类，兼容层整组 applicable=False）；A 组 9 项双向判真伪（思考/代码执行/搜索接地/受控结构化输出/缓存/logprobs/安全评级/token 计数一致性/多模态时间戳，supported→PASS 证真、声称却缺特有字段→FAIL/DEGRADED 证伪、上游 400→skipped 不计负分）+ B 组 4 项单向确证（URL Context/Vertex RAG/Maps 接地/SafetySeverity，supported→PASS 一票确证、任何不支持→skipped 不扣分）；适配器补 logprobs_result 指纹提取，共 38 项单测通过
- [x] 真实性评分模型（加权 + 分级阈值，阈值可调）—— **Task 17-20 已完成**：`SignalAggregator`（无罪推定从满分扣减：refute 减 Σ(w×sev×conf)、confirm 加回补系数封顶 100 + 逐信号贡献明细）、`AuthenticityScorer`（shell/direct 双子分 + Gemini A 组 PASS 桥接强证真/FAIL·DEGRADED 证伪、B 组 PASS 一票确证锁定 ≥H + 取短板 min）、`ScoreAggregator`（维度加权平均 skipped 不计分母/degraded 半权/fail 计 0 + 真实性维度注入短板值 + 连通性短路标不可用 + 五维度加权总分）、`ConfidenceGrader`（compat×0.6/样本覆盖率折算/单信号误报控制 + 三级阈值 H75/L45 可调），共 56 项单测通过
- [x] 报告可视化（ECharts）—— 后端 **Task 23 已完成**：`ReportService` 三层结果装配（结果汇总+策略明细+探针下钻）+ 结果/策略/探针记录端点；**前端 Phase 6 已完成**：`BaseChart` 通用封装（暴露 getDataURL 供 PDF 截图）+ `chart_config.js`（五维雷达/真实性双子分柱/性能趋势）
- [x] PDF 导出（weasyprint）—— **Task 25 已完成**：`PdfService` Jinja2 九区块 A4 模板（CJK 字体/真实性专章/信号贡献表/检测完整性）+ weasyprint 转 PDF + 前端图表 base64 内联；POST 带图表 / GET 纯数据回退端点，PDF 魔数校验 + 不泄露 Key，共 7 项测试通过

---

## 阶段六：前端实现（Vue 3 + Naive UI + ECharts）

- [x] 应用根布局与导航（Task 29）—— `App.vue`：n-config/message/dialog Provider 包裹 + 顶部菜单（中转站管理/检测任务，报告页归属任务模块高亮）+ 路由出口；主色微调
- [x] 中转站与模型管理（Task 30）—— `StationsView`（折叠列表 + 新建/编辑弹窗 + 删除二次确认）+ `StationForm`（协议集合多选/自定义地址/Key 编辑态留空不改）+ `ModelPanel`（自动拉取逐协议成败反馈 + 失败回退手动录入 + 模型表）
- [x] 检测任务与 SSE 进度（Task 31）—— `TasksView`（任务列表 + 状态标签 + 跟踪/取消/查看报告）+ `TaskForm`（站点→模型级联选择 + 预算/声明能力高级配置）+ `TaskProgress`（订阅 SSE 实时总进度 + 逐探针状态网格 + 评分/失败原因）
- [x] 图表与报告（Task 32-33）—— `ReportView`（综合得分卡片 + 真实性研判专章含分级/置信度/信号贡献表 + 五维雷达/真实性柱/性能趋势图 + 策略明细表下钻探针抽屉 + PDF 导出截图内联）+ `BaseChart` + `chart_config.js`
- [x] 工程化 —— 清理遗留空目录 `app/scoring/`（真实代码在 `backend/app/`）；`npm install` + `vite build` 通过；echarts/naive-ui 拆为可缓存 vendor 分包，页面 chunk 各约 10kB
- [ ] 前后端联调（启动后端 + `npm run dev` 走通建站→拉模型→检测→报告→导出全链路）

---

## 阶段五：测试与交付

- [x] 探针单元测试 —— 各探针均随实现配套单测（连通性/性能/计费/能力/真实性/Gemini 功能性指纹），共 100+ 项
- [x] 评分模型测试 —— 信号加权/双子分/维度总分/置信度分级共 56 项单测
- [x] 适配器测试（含 Gemini 原生/兼容层双路径用例）—— Task 6-8 共 47 项
- [x] 端到端检测流程验证 —— **Task 27 已完成**：真实栈（建站→录模型→建任务→真实探针执行→真实评分编排→三层落库→报告装配→PDF 导出，仅 adapter 替身）验证落库完整性/评分计算/脱敏/PDF 产出，共 2 项 e2e
- [ ] 文档完善与交付
