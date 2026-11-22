# MiniApp AI-native 增强开发计划

> 日期：2026-11-22
> 分支：`feature/miniapp-capabilities`，schema v48（046/047/048 已应用）
> 口径：本文是**开发计划**，不是现状。现状以 [MiniApp 功能与架构现状](./miniapp-overview.md) 为准；计划中的“已完成/未完成”以代码与测试为准，不把设计稿当事实。
> 关系：承接 `miniapp-overview.md` §4/§7、`miniapp-product-plan.md` §3.3 的 MVP 验收线、`miniapp-use-cases-and-security.md` §0.1 的已知缺口、`miniapp-scheduler-review.md` 的必修项。

---

## 0. 结论摘要

**目标**：把 MiniApp 从“AI 写的应用”推进到“AI-native 应用”——Agent 能**创建、运行、治理、观察、迭代**应用；页面能发起有界 AI 任务；执行由可版本化 profile 驱动；消耗可归因、可预算、可审计。

**基线**：`feature/miniapp-capabilities`，schema v48；PR-11/12 已部署。创建、数据、权限、分发较强；AI 运行时、自演进、生产硬化是短板。

**团队假设**：2 后端 + 1 前端 + 产品/设计兼职 + 0.5 安全/QA；核心 v1 约 **13–17 周**。

| 阶段 | 时间 | 目标 | 出门条件 |
|---|---:|---|---|
| **P0 地基硬化** | W1–2 | 修会放大风险的生产阻塞 | `make all` 绿 + 回归用例 |
| **P1 AI 运行时核心** | W3–6 | 应用能发起有界 AI 任务 | 端到端任务 + 隔离 + 预算 |
| **P2 首个场景** | W7–9 | 审核参考应用跑通 | 3 个设计伙伴使用 2 周 |
| **P3 治理与演进** | W10–13 | 可对外、可观察、可迭代 | 安全评审 + 压测 + 文档 |
| **P4 分发与互操作**（可选） | W14–17 | 应用能离开单实例 | 外部需求验证 |

**一句话策略**：先把会放大风险的生产阻塞收口，再做“分级执行 + 可版本化 profile”的 AI 运行时，用一个真实审核场景验证，最后补治理、观察和分发。不要先做 MCP Apps、公开看板或市场。

---

## 1. 目标与边界

### 1.1 AI-native MiniApp 的五条能力

| 能力 | 目标状态 | 当前 |
|---|---|---|
| **创建** | 自然语言 → 数据表 + 页面；快速模式；Setup Plan | ✅ 强 |
| **运行** | 页面发起 Tier 0–3 AI 任务；profile 驱动；结果写回 | ◐ 只有只读助手 + 一次性生成 |
| **治理** | ACL/行级 PII/HITL/能力投影/审计 | ✅ 强，但有两处暂缓缺口 |
| **观察** | 按 app/task 的用量、成本、错误、健康度 | ⛔ 只有全局指标 |
| **演进** | profile/发布版本化；Agent 能诊断并迭代应用 | ⛔ 只有单版 HTML 回滚 |

### 1.2 明确不做

- 不做通用 workflow runner / 任意服务端 JS。
- 不做多页 React / 构建链。
- 不把 MiniApp 做成独立 A2A agent。
- 不做权威业务数据库。
- 不做公开可写链接。
- 不重写 MiniApp 为 MCP Apps 客户端（只做门面）。

---

## 2. 现状基线与差距

### 2.1 维度评分

评分只用于表达相对强弱，不是精确度量。

| 维度 | 评分 | 判断 | 主要缺口 |
|---|---:|---|---|
| 创建 / Agent 工具 | 4.5/5 | 最扎实 | 云端市场占位；无发布历史 |
| 数据底座 | 4.0/5 | 接近可用 | P0-4 查询下推、P0-5 页面上传 |
| AI 运行时 | 2.0/5 | 最大落差 | 不能发起 Agent 任务；无 per-app 会话/模型 |
| 治理 / 身份 | 4.0/5 | 强 | prepared operation、page-facing 能力覆盖、备份越权 |
| 外部连接 | 3.5/5 | 可用 | promote、调度硬化、无双向实时 |
| 分发 | 3.5/5 | 可用 | 公开看板、Bundle v2、MCP 门面 |
| 安全隔离 | 3.5/5 | 纵深防御 | 同 renderer process；非 Chromium 未回归 |
| 运维 / 演进 | 2.5/5 | 最弱 | 调度必修项、per-app analytics、页面错误上报 |

### 2.2 三个必须记住的反面证据

1. **调度必修 #2–#4 仍未修**：`src/octop/infra/apps/scheduler.py:113-128` 复用起始 `now`；`:145-165` 心跳一次异常就停；`038_light_app_schedules.sql` 没有 `snapshot_publication` 的部分唯一索引。
2. **备份行级越权**：`src/octop/api/routers/apps.py:3572` 只要求 `edit_structure`，而 `src/octop/infra/apps/service.py:5921` 对所有表 `include_all_rows=True`；`edit_structure` 协作者可以下载 `row_scope=user` 表里其他用户的行。
3. **完整 `make all` 尚未拿到一次全绿记录**：`docs/miniapp-overview.md:1105` 记录全量 pytest 在本机跑到 88% 因资源被中止。

这三项在 P0 必须先处理，否则后续 AI 任务会把风险放大。

### 2.3 这次计划不重复造的东西

- **Agent 运行时**：复用 `AgentManager.stream`、harness、模型工厂、`UsageTracker`、HITL。
- **任务队列**：`light_app_tasks` 是任务状态表，不是新的全局事件总线；事件触发的任务复用 `infra/events`。
- **权限内核**：复用 `infra/apps/access.py` 的 `authorize()`；任务只增加 action 和 task scope。
- **工作区 I/O**：仍走 `HarnessAgent.workspace`。
- **计费**：复用 `usage_log`，加 `app_id` / `task_id` / `billing_subject` 等维度，不另建一套账本。

---

## 3. 目标架构

### 3.1 分层

```text
MiniApp 页面 / API
   │  task_type, row_id, input
   ▼
AppTaskService（app 运行时：权限、任务类型、预算、审计、结果 schema）
   │
   ▼
TaskRouter（按 task profile 选层级）
   ├── Tier 0：确定性执行（无 LLM）
   ├── Tier 1：单次模型调用 + 结构化输出
   ├── Tier 2：受限 Agent（共享 AppTaskRuntime）
   └── Tier 3：owner expert / 专属 agent 接力（HITL）
   │
   ▼
UsageService / BudgetService（计量、预扣、结算、成本）
   │
   ▼
AppService（CAS、行历史、PII、审计）
```

### 3.2 六个关键决策

1. **App 运行时属于 `AppTaskService`，executor 可替换**。不要让 app 的运行时等于某个 expert 的进程。
2. **Owner expert 是 profile 作者 + Tier 3 升级路径，不是默认执行器**。默认 Tier 1/2 用共享 runtime。
3. **任务类型命名、schema 绑定、profile 版本化**。页面不能传任意 prompt。
4. **能力投影**：任务 agent 只拿 `app_read_row` / `app_write_task_result` / 声明只读服务；不给 shell、文件、任意 MCP。
5. **App-scoped 上下文**：独立 task thread 或 stateless；不碰 owner 的记忆/工作区/聊天 thread。
6. **App owner 付费；funding source 显式**。执行者不是付款人；不允许静默用专家 Key 或平台 Key 兜底。

### 3.3 Task profile

每个任务类型绑定一个可版本化的 profile，放在代码注册表（`infra/apps/task_types.py`）中，应用只选择并 pin 版本：

```json
{
  "task_type": "pre_review",
  "tier": 1,
  "model_ref": "fast-model",
  "prompt_template": "...",
  "input_schema": {"row_id": "string"},
  "output_schema": {
    "summary": "string",
    "verdict": "enum(approve, reject, needs_review)",
    "confidence": "number"
  },
  "allowed_tools": [],
  "writes": ["ai_summary", "ai_verdict", "ai_confidence"],
  "max_tokens": 2000,
  "timeout_seconds": 60,
  "profile_version": 1,
  "authored_by": "expert:content-review"
}
```

- owner expert 是 profile 的作者/维护者，不是运行时依赖。
- app owner 选择并 pin `profile_version`；专家更新 profile 不会静默改变已 pin 的应用。
- profile 随 app 导出/导入（Bundle v2），迁移时不需要迁移 expert。

### 3.4 四级执行

| 层级 | 典型任务 | 执行方式 | 成本/延迟 | 能力位 |
|---|---|---|---|---|
| **Tier 0** | 导入映射、聚合、校验、PII 脱敏、快速视图 | 确定性代码，不调 LLM | 最低 | 始终可用 |
| **Tier 1** | 摘要、分类、抽取、打分、翻译、单条改写 | 一次模型调用 + JSON schema 校验 | 低 | `ai.generate` |
| **Tier 2** | 跨行分析、线索富化、简单多步处理 | 受限 agent 回合，只绑 app 工具/声明服务 | 中 | `ai.agent_task` |
| **Tier 3** | 行业研究、复杂内容生产、多系统操作 | owner expert / 专属 agent 接力，HITL | 高 | `ai.expert_task`（owner 开启） |

---

## 4. 工作流

| 工作流 | 范围 | 主要交付 | 依赖 | 阶段 |
|---|---|---|---|---|
| **WS1 地基硬化** | 调度、备份、能力覆盖、全量验证 | 3 个必修修复 + 回归 + CI 绿 | 无 | P0 |
| **WS2 AI 运行时** | task 表、profile、Tier 1/2、executor、API/SDK | `light_app_tasks`、`AppTaskRunner`、任务卡 | WS1 | P1 |
| **WS3 场景与 UX** | 审核参考应用、上传、查询下推、任务 UI、通知 | 端到端审核应用 | WS2 | P2 |
| **WS4 资源治理** | 归因、预算、预扣、成本、showback | `usage_budgets`、`usage_reservations`、用量页签 | WS2 | P1–P3 |
| **WS5 治理与演进** | HITL、prepared ops、analytics、错误上报、发布历史、per-app model | 可供多用户/对外使用的闭环 | WS2/WS3 | P3 |
| **WS6 分发与互操作** | 公开看板、Bundle v2、MCP Apps 门面 | 跨实例/跨宿主分发 | WS1–WS5 | P4 可选 |

---

## 5. 阶段计划

### P0：地基硬化（W1–2）

**目标**：不加用户功能，先修会放大 AI 任务风险的生产问题。

| 任务 | 交付 | 验收 |
|---|---|---|
| 调度必修 #2–#4 | 每 claim 取新 `now`；开始即续租；心跳重试；租约丢失取消；snapshot 部分唯一索引 | 长 tick 后第二 claim 未过期；心跳瞬时异常仍续租；并发建 snapshot 只留一条 |
| 备份 ACL | owner/admin 才能完整归档，或按 viewer 过滤 `row_scope=user` | 协作者备份不含他人 user 行 |
| page-facing 能力覆盖 | 端点 ↔ 能力表 + 未登记即失败的测试 | 新增桥方法/端点必须登记 |
| 全量验证 | `make all` + dashboard build + Linux/Windows CI | 一次全绿记录 |

**出门条件**：上述测试全绿；无新增安全发现；`make all` 绿。

### P1：AI 运行时核心（W3–6）

**目标**：页面能发起一个有界、可审计、可取消的 AI 任务，并把结构化结果写回数据。

| 任务 | 交付 |
|---|---|
| 任务模型 | `light_app_tasks`（049+）：task_id/status/input/result/error/actor/executor/model/attempts/lease/idempotency |
| Profile | `infra/apps/task_types.py` 代码注册表；profile 版本化 + app pin |
| Tier 1 | 单次模型调用 + JSON schema 校验 + 重试 + 写声明列 |
| Tier 2 | 共享 `AppTaskRuntime`，复用 `AgentManager.stream`；三个窄工具；max rounds/tokens/timeout |
| API/SDK | `POST/GET/cancel /apps/{id}/tasks`；`octopApp.runTask/getTask/cancelTask/onTaskChange` |
| 治理 | 能力位 `ai.agent_task`（owner 开启）；公开链接默认关闭；审计 `miniapp.agent_task.*` |
| 资源治理 | `usage_log` 加 `app_id/task_id/billing_subject/funding_source`；每任务预算 + 预扣/结算；`source='app_task'` |
| 测试 | 成功/失败/超时/取消/重复提交/跨用户隔离/预算耗尽 |

**v1 范围**：只写 AI 派生列，不触发外部副作用，因此不接 HITL；需要审批的写在 P3。

**出门条件**：端到端任务闭环；无跨用户泄漏；成本不超过 profile 预算；内部 demo 通过。

### P2：首个 AI-native 场景（W7–9）

**推荐场景**：审核/审片（也可换成线索富化，取决于设计伙伴）。

| 任务 | 交付 |
|---|---|
| 页面上传（P0-5） | `octopApp.upload(file)` + 宿主桥 + 大小/类型白名单；先图片/PDF，视频先关键帧 |
| 查询下推（P0-4） | `json_extract` 过滤/排序；2 万行 ≤50ms；SQLite/PG 语义一致 |
| 任务 UI | 队列、状态、重试、取消、结果 diff、通过/驳回、失败原因 |
| 通知 | 任务完成/失败推送到桌面/IM；复用 `notifications` |
| 参考应用 | 内容审核台模板 + AGENTS.md + 验收脚本 |
| Showback | 应用详情“用量与成本”页签：任务数、tokens、成本、失败率、p95 |

**验收**：20 条真实内容；AI 初审 p95 <60s；人工审核时间下降 ≥30%；PII/越权事故 0；3 个设计伙伴使用 2 周。

### P3：治理与演进（W10–13）

| 任务 | 交付 |
|---|---|
| Tier 3 / HITL | owner expert 接力；`waiting_approval`；批准后从 checkpoint 恢复 |
| Prepared operation | 删列/清 PII/`include_pii`/公开分享的审批绑定 payload hash |
| Per-app analytics | 按 app 的打开、写入、任务、失败、p95、tokens、成本；Agent 可读 |
| 页面错误上报 | iframe 未处理异常 + 任务失败 → `light_app_events` / 诊断工具 |
| 发布历史 | `light_app_publication_versions`；不再只有 `previous_html` |
| 能力版本 | `CAPABILITIES_VERSION` 写入 manifest / `me()` |
| 按应用选模型 | `light_apps.agent_model_ref`；任务类型可覆盖 |
| 按用户会话 | 需要记忆时加 per-app+user thread；严禁跨用户共享 |
| IM per-sender | 如 IM 是主入口，再做 per-sender thread/memory |

**出门条件**：安全评审通过；10× 压测；`make all` 绿；`/api/docs` 可读；i18n 键齐全；关键路径回归。

### P4：分发与互操作（W14–17，可选）

| 任务 | 前置条件 |
|---|---|
| 公开看板 | 单应用公开链接已满足不了需求 |
| Bundle v2 | 客户需要跨实例迁移应用 |
| MCP Apps 门面 | 有外部宿主需求；`ui://` 匿名/短时 token 访问方案批准 |

**原则**：做门面，不重写数据模型；不在核心闭环稳定前做分发。

### 5.6 排期与并行

| 周 | 后端 | 前端 | 产品/设计 | 安全/QA |
|---|---|---|---|---|
| W1–2 | 调度/备份/能力覆盖 | 回归 + 任务卡 mock | 场景与验收口径 | 备份 ACL 复核 |
| W3–6 | 任务表/runner/预算 | 任务卡/SDK | profile 设计 | 预算/隔离评审 |
| W7–9 | 上传/查询下推 | 审核应用 UI | 设计伙伴运营 | 沙箱/上传评审 |
| W10–13 | HITL/analytics/发布历史 | 用量页签/错误上报 | 验收与文档 | 安全评审 + 压测 |
| W14–17 | 公开看板/Bundle/MCP | 公开页 | 外部需求验证 | 匿名/token 访问评审 |

---

## 6. 数据与接口

### 6.1 迁移（049+，按合并顺序编号）

| 迁移 | 内容 |
|---|---|
| 049 | 调度 snapshot 部分唯一索引（若 038 已发布） |
| 050 | `light_app_tasks` |
| 051 | `light_app_task_profiles` + `usage_log` 扩展 + `usage_budgets` + `usage_reservations` |
| 052 | `light_app_publication_versions` + `light_apps.agent_model_ref` / `capabilities_version` |
| 053 | `model_prices`（可选） |

所有迁移必须 SQLite + PG 双份，同步 `migrate.py` 幂等 helper 与 `tests/unit/db/test_db_pool.py` 版本断言。

### 6.2 关键表

| 表 | 作用 |
|---|---|
| `light_app_tasks` | 任务状态、输入/结果、actor/executor/model、租约、幂等 |
| `light_app_task_profiles` | profile 版本、prompt、schema、工具白名单、预算 |
| `usage_budgets` | 按付款主体/app/窗口的限额与已用/预扣 |
| `usage_reservations` | 任务级预扣、结算、过期释放 |
| `light_app_publication_versions` | 发布历史 |
| `model_prices`（可选） | 模型价格与版本 |

### 6.3 API

```text
POST /api/apps/{id}/tasks
GET  /api/apps/{id}/tasks/{task_id}
GET  /api/apps/{id}/tasks?table=&row_id=&status=
POST /api/apps/{id}/tasks/{task_id}/cancel
GET  /api/apps/{id}/usage?window=&group_by=task_type|user|day
GET  /api/apps/{id}/budget
PUT  /api/apps/{id}/budget
GET/PUT /api/apps/{id}/task-profiles
```

### 6.4 SDK

```js
octopApp.runTask({ taskType, rowId, input })
octopApp.getTask(taskId)
octopApp.cancelTask(taskId)
octopApp.onTaskChange(cb)
octopApp.upload(file)
octopApp.me() // 含 capabilities_version
```

### 6.5 能力位

- `ai.agent_task`：Tier 2，默认关闭，owner 开启。
- `ai.expert_task`：Tier 3，默认关闭，owner 开启 + HITL。
- `asset.write`（或 `upload`）：页面上传，默认关闭。
- 公开链接默认拒绝以上能力。

---

## 7. 资源治理与计费

### 7.1 三层分离

- **计量**：每次模型调用写 append-only usage，带 `app_id` / `task_id` / `billing_subject` / `funding_source` / token 分桶 / `cost_micros` / `price_version`。
- **配额**：任务、应用、用户/租户、执行器四层预算；先预扣、后结算。
- **计费**：默认 app owner 付费；executor 不是付款人；BYOK 与平台 Key 分开。

### 7.2 付款人与资金源

| 场景 | 付款人 | 说明 |
|---|---|---|
| 默认 | app owner | 与现有 `assistant.py:220-224` 一致 |
| 协作/共享应用 | app owner | 协作者使用，owner 付费；加每用户/每应用上限 |
| 租户/团队应用 | 租户 billing account | 未来扩展 |
| BYOK | Key 所属方 | Octop 只计量 tokens、做公平配额 |
| 平台 Key | app owner 的 plan/预算 | Octop 计算成本，可出账单 |
| Tier 3 owner expert | app owner | 使用 app 的 billing profile；专家 Key 需显式 opt-in |
| guest / public | app owner | 默认关闭；开启后硬上限 |

当前 `providers` 是全局表，没有 `user_id`（`001_initial.sql:52-63`）；真正的 BYOK 需要按用户/租户解析 Key。无论哪种模式，都要记录 `funding_source`，禁止静默降级。

### 7.3 配额层

| 层级 | 目的 | 默认 |
|---|---|---|
| 每任务 | 防单任务失控 | `max_tokens`、`max_micros`、`timeout`、`max_rounds` |
| 每应用/天 | 防一个应用刷爆 owner | owner 可配 |
| 每应用/月 | 预算主闸门 | 按 plan 或 owner 设置；硬停或软超 |
| 每用户/每应用/天 | 防单个协作者耗尽 | 例如 20 次/天 |
| 每付款主体/月 | 租户/用户账单 | 月度窗口；终身 quota 作为硬上限 |
| 每执行器 | 共享 runtime 并发 | 全局并发 + 队列深度 |
| 每模型/供应商 | RPM/TPM/并发 | 按 provider 限流 |
| guest / public | 防匿名烧钱 | 默认关闭 |
| 非 LLM 资源 | 行数/存储/任务数/并发 | 复用现有 12 表 / 2 万行 / 5MB / 50 应用 |

### 7.4 预扣与结算

```text
创建任务
  → 按 profile 估算最大 tokens/成本
  → DB 原子预扣（app / subject / task）
  → 执行模型调用（每次调用按 task_id + attempt + call_id 幂等记账）
  → 结算：实际 vs 预扣，释放剩余
  → 写最终 usage / 任务结果
```

- 预留失败直接拒绝，不做 check-then-spend。
- 多实例只用 DB 条件 UPDATE；进程内 `limits.py` 只做流量保护。
- 超时/取消：已发生调用照扣，未发生释放。
- HITL 等待不扣费；恢复执行才扣。
- 记账失败不能静默；付费任务同事务或 outbox。

### 7.5 无人值守消费

cron、主动专家、团队 autopilot、事件触发的 `app.agent_turn`、后台重试都可以在没有人在场时花钱。需要：

- 预算感知调度：预算不足就不启动/排队/降级。
- 复用现有 `team_runtime.budget_used` / `paused_budget` 的原子计数和暂停语义。
- kill switch（按 agent/app/用户/全局）。
- 异常消费告警：突增、重复重试、超长上下文、异常工具调用。

### 7.6 非 token 资源

账本用通用 `resource_type`（`llm_tokens` / `tool_call` / `embedding` / `image` / `sandbox_second` / `storage_byte`），token 只是其中一种。先 token + 一个通用列，等真实账单出现再扩展。

### 7.7 Showback 先于 chargeback

- 自托管部署最小可用：计量 + 配额 + 可见性 + BYOK；不要一开始做发票、税、退款、信用额度。
- 先 showback 跑 2–4 周验证数据，再软限，再硬限。
- 未定价模型标 `pricing_status='unpriced'`，不要当 0 成本。

---

## 8. 指标与门禁

**North star**：每周有 ≥1 次成功 AI 任务的活跃 MiniApp 数。

| 类型 | 指标 | 目标 |
|---|---|---|
| 采用 | 周活跃应用、任务数/应用、设计伙伴留存 | 逐阶段提升 |
| 质量 | 任务成功率、p95 时长、取消率、重试率 | 成功率 >95%；p95 <60s（审核场景） |
| 成本 | 每任务 tokens/成本、缓存命中率、预算超支数 | 超支 0；成本可解释 |
| 安全 | PII 事故、越权、审批绕过、公开写 | 全部 0 |
| 工程 | `make all`、集成测试、API docs、i18n、Windows CI | 每阶段绿 |

**阶段门禁**：

- P0：`make all` 绿 + 三项回归用例通过。
- P1：任务端到端 + 跨用户隔离 + 成本上限用例通过。
- P2：3 个设计伙伴使用 2 周，成功率 >95%，事故 0。
- P3：安全评审 + 10× 压测 + 文档/API/i18n 完成。
- P4：外部需求已验证，匿名/token 访问方案批准。

---

## 9. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| Scope creep | 计划失控 | 阶段门禁；P4 可砍 |
| Owner expert 耦合 | 可用性/计费/隔离问题 | profile + 共享 runtime；expert 只做 Tier 3 |
| 成本失控 | 烧预算 | Tier 分级、max rounds/tokens/timeout、预算预扣、kill switch |
| Prompt injection | 越权/昂贵循环 | 能力投影、内容当数据、schema 校验、HITL |
| 多实例不一致 | 超配额/重复扣费 | DB 原子预扣；进程内限流只做流量保护 |
| 测试 flaky | 无法验收 | fake executor；确定性 profile 测试 |
| 文档漂移 | 误判完成度 | 每阶段更新 overview/product plan/API docs |
| 全量测试不绿 | 无法合并 | P0 先拿到一次 CI 全绿 |

---

## 10. 交付物与文档

- **ADR**：任务运行时与 Tier 分级；owner expert 定位；billing subject/funding source；profile 版本化；任务/事件平面边界。
- **文档**：更新 `miniapp-overview.md`、`miniapp-product-plan.md`、`event-model.md`、`api.md`；本文作为计划单一入口。
- **迁移**：049–053（按实际合并编号），SQLite/PG 双份。
- **测试**：单测 + 集成 + 浏览器沙箱 + 跨库 + 故障注入。
- **运维**：应用用量/成本页签、预算告警、任务健康度、未定价模型告警。

---

## 11. 启动清单（前 10 个工作日）

- **Day 1–2**：两份 ADR（任务运行时、资金源/付款人）；选定审核场景与设计伙伴；定义 `pre_review` input/output schema。
- **Day 3–5**：050 迁移 + `light_app_tasks` repo/service 骨架；任务 API 契约；前端任务卡 mock。
- **Day 6–8**：`AppTaskRunner` + `AgentManager.stream` 接入 + 幂等/超时/租约；单测。
- **Day 9–10**：SDK `runTask` + 宿主桥 + 集成测试；内部 demo（假模型即可）。
- **并行**：P0 的调度/备份修复由另一名后端推进。

---

## 12. 待确认决策

1. **首个场景**：审核/审片，还是线索富化/客户跟进？
2. **Tier 3 策略**：owner expert 接力，还是允许专属 app agent？
3. **商业模式**：自托管 showback 即可，还是要做真正的 billing/plan？
4. **公开/guest 任务**：默认完全关闭，还是允许 owner 显式开启？
5. **团队与时间**：13–17 周是否可接受，是否需要先砍 P4/部分 P3？

---

## 13. 不做什么

- 不做通用 workflow runner / 任意服务端 JS。
- 不做公开可写链接。
- 不重写 MiniApp 为 MCP Apps 客户端。
- 不在任务运行时稳定前做市场、公开看板、Bundle v2。
- 不在单实例任务运行稳定前做多实例/全局限流。
- 不在没有真实使用前做 per-app 自定义后端服务。

---

## 14. 进度（2026-11-22）

| 阶段 | 状态 | 已落地 |
|---|---|---|
| **P0 地基硬化** | ✅ | 调度必修 #2–#4；备份仅 owner/admin；能力覆盖测试（SDK 调用 ↔ 能力映射）；迁移 049 |
| **P1a AI 运行时核心** | ✅ | `light_app_tasks`（050）；`summarize_row` Tier 1；任务 API/SDK（`runTask` / `getTask` / `cancelTask`）；`usage_log.app_id/task_id`（051）；任务审计、幂等、过期租约回收 |
| **P1b 受限 Agent** | ✅ | `table_digest` Tier 2；只读 `query_rows` / `aggregate` 工具（per-table 重新鉴权、PII 脱敏、行 scope）；max rounds / tool calls；每应用每日 AI token 上限（200k，简单闸门，非预扣） |
| **P2a 任务 UI** | ✅ | 应用详情新增「AI 任务」页签：列表、状态、结果、取消、活动任务 3 秒轮询；i18n 双语 |
| **P2b-1 创建入口** | ✅ | 4 个 Agent 任务工具（`app_run_task` / `app_get_task` / `app_list_tasks` / `app_cancel_task`）；`GET /apps/{id}/task-types`；数据页行操作「AI 任务」+ `RunTaskModal`（选类型/写列），创建后跳到任务页签 |
| **P2b-2a 页面上传** | ✅ | `asset.write` 能力位；`octopApp.upload(file)` → 宿主桥 → `POST /assets?page=true`；PNG/JPEG/WebP/GIF，≤5MB；服务端魔数校验 + ACL + 能力位 |
| **P1b / P2 余量** | 未开始 | `usage_budgets` / `usage_reservations` 预扣；任务 HITL；查询下推（P0-4）、审核参考应用、通知；per-app 模型/profile 版本 |

**验证（P0 + P1a + P1b + P2）**：`make lint`、`make typecheck` 通过；`tests/unit/apps` 525 passed；`tests/unit/db` 245 passed；`tests/unit/agents/test_agent_manager.py` 155 passed + 1 skipped；`tests/integration/test_apps_api.py` 80 passed；前端任务相关 6 passed、`MiniAppFrame` 62 passed；`tsc -b`、ESLint 通过。完整 `make all` 仍待 CI/停服窗口补跑。

**当前限制**：Tier 1 单次模型调用；Tier 2 是应用内只读工具循环，不是完整 owner expert；任务按单车道串行执行，超时（Tier 1 60s / Tier 2 120s）短于租约（300s），尚无心跳；每日 token 上限是 check-then-run，不是预扣；任务结果写回使用行 CAS，冲突即失败，不自动重试。
