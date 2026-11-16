# 业务 AgentOS 实施路线（Route A）

Status: decision record / roadmap
Date: 2026-11-15
Status review: `docs/agent-os-status-review.md`（2026-11-16 状态复盘与必做清单）
Scope: 把 Octop 收敛为「单节点、自托管、面向具体业务的 AgentOS」。
桌面 Shell 是它的 UI 隐喻，不是内核；多租户、多节点、通用第三方生态不在本路线内。

相关文档：
- `docs/agent-os-overall-plan.md` —— 业务 AgentOS 的设计记录（五原语、双模执行、治理平面）
- `docs/architecture-overview.md` —— Octop 现状与差距分类
- `docs/event-model.md` —— 事件平面与已知正确性缺口
- `docs/miniapp-product-plan.md` —— MiniApp 的定位、非目标与数据 P0
- `docs/digital-employee-plan.md` —— 数字员工/团队自主的现状与缺口

---

## 0. 决策

**Adopt Route A：单节点业务 AgentOS。**

三句话：

1. **目标形态**：一个盒子里跑完整 Octop；老板给带边界的目标，内核负责安全执行、审批、审计与成本；MiniApp 是展示层，不是权威源。
2. **当前差距**：五个内核原语里，目标入口、执行运行时已可用；能力合约、审计链只有一半；边界策略引擎缺失。
3. **实施顺序**：治理内核 → 执行内核 → 应用内核 → 可观测/核算 → 生态/联邦（条件触发）。

### 0.1 非目标（明确不做）

| 非目标 | 原因 |
|---|---|
| 多租户 SaaS | 没有 tenant 维度；当前正确形态是每客户/每门店一个实例 |
| 多节点集群 / 分布式内核 | 与 ADR 001 单进程、ADR 002 单写者冲突；跨节点用「每节点一个 Octop + Bridge 协调」 |
| 通用第三方代码运行时（原生小程序） | MiniApp 是零后端、单文件 HTML、宿主代调；不执行服务端页面代码 |
| 用 MCP Apps 替换数据与治理层 | 它只替换界面/沙箱层；数据、权限、审计仍须由 Octop 提供 |
| 微服务 / 外部队列 | 与单进程自包含的产品前提冲突；先做好单进程内的隔离与配额 |
| 继续堆桌面 Shell 的窗口特效 | Shell 已够用；当前瓶颈在内核，不在橱窗 |

### 0.2 四条不变量（沿用 `agent-os-overall-plan.md` §13.4）

1. **规划与执行分离**：Planner 无工具，Dispatcher 确定性，Worker 受合约约束。
2. **审批权只在内核**：业务层不能拥有审批权，不能绕过边界策略。
3. **展示层不是权威源**：MiniApp 只做投影；权威数据在原生表与账本。
4. **节点自包含**：不为了跨节点共享状态而牺牲断网可用性。

---

## 1. 现状基线（2026-11-15）

### 1.1 内核五原语

| 原语 | 现状 | 判定 |
|---|---|---|
| 目标入口 | `team_goals` + autonomy API + `bounds` 尚缺绑定 | ✅ / ⚠️ |
| 执行运行时 | `octop-harness` + `AgentManager`；单进程、可选 Docker | ✅ |
| 能力合约注册表 | 只有工具级 `SecurityPolicy`；合约 schema 未建 | ⚠️ |
| 边界策略引擎 | 无「放行 / 推送审批 / 拒绝」三态 | ❌ |
| 审计链 | `audit_log` 覆盖管理动作；工具调用、行写入无审计 | ⚠️ |

### 1.2 七个结构性缺口（详见本轮评估）

| # | 缺口 | 关键事实 | 影响 |
|---|---|---|---|
| D1 | 执行隔离与资源治理缺失 | 单进程，Agent 是内存对象；默认 backend 是 host-rooted `local_shell`；Docker 可选；无 per-agent 并发/内存/墙钟配额 | 一个失控循环可拖垮全节点 |
| D2 | 权限是用户 ACL + 前端能力位 | iframe 无独立身份，桥调用使用当前用户 JWT；服务端只对 3 类能力二次校验；工具调用无审计 | 无法回答「谁用什么权限做了什么」 |
| D3 | 调度/自主执行语义碎片化 | 五套循环；只有 events/app schedule 有 DB 租约；无全局优先级/背压；self-execute HITL 未接线 | 无法证明至少一次 + 幂等 |
| D4 | 成本闸门滞后、局部 | 团队 token 预算 opt-in；无 `paused_budget`；在途回合可超支；proactive 调用不计费 | 账单与死循环保护不足 |
| D5 | 应用不是「包」，扩展面碎片化 | 只有当前 HTML + 一版回滚；无版本/签名/市场；9 种扩展面、两种信任模型 | 生态无法成立 |
| D6 | 控制面/数据面同库、单写者 | SQLite 单连接 + RLock；PG 单写者承诺；无 tenant；app 行 document-in-row | SaaS/多租户无物理基础 |
| D7 | 可观测/审计/健康检查最低配 | 进程内计数器、文本日志、health 恒 `ok`、审计可删除 | 无法计量、追溯、告警 |

### 1.3 不要推翻的资产

- 单进程自包含（对边缘盒子是正确取舍）。
- 依赖向内、repo 只做 SQL、运行时外置（harness/gateway/memory）。
- 五类事件的契约化：at-least-once、幂等键、payload 不选 target、WS 不是真相。
- 多用户 ACL、MiniApp 能力清单 + 双层沙箱、专家/团队模板、运维内建（setup/TLS/backup/systemd）。
- 团队编排的 bounded-agentic 设计：Planner 无工具 + 确定性 Dispatcher + 账本。
- 桌面 Shell 明确「不新增后端权限」；问题在状态模型，不在越权。

---

## 2. 目标架构（不重复 `agent-os-overall-plan.md`）

```
交互层      Dashboard / IM / CLI / 桌面 Shell（UI 隐喻）
   │
目标接入    目标 + bounds（预算/范围/必须审批的动作）
   │
   ├─ 路径 A：确定性工作流（cron + task 依赖；工作流引擎按需）
   └─ 路径 B：Planner（无工具）→ 四道闸 → Dispatcher（确定性）
   │
执行层      Worker 池（隔离会话 + 执行信封）
   │
治理平面    能力合约 · 边界三态 · 能力审计 · 成本硬顶
   │
账本/存储   team_decisions / team_events / team_tasks / audit / usage
   │
展示层      MiniApp（投影层，非权威源）
```

**关键点**：治理平面是横向内核，不是新的调度层；它挂在工具执行钩子上，位于 `SecurityPolicy` 之上，先粗判（90% 放行），越界才下沉到精判与 HITL。

---

## 3. 里程碑

> 下面只定义**顺序、范围与验收**，不排具体人日；排期按团队规模另定。
> M1 与 M2 可以在人力允许时并行，但 M1 的能力合约是 M2 边界判断的前置。

### M0 · 定位冻结（先于一切）

| 项 | 交付 |
|---|---|
| 决策记录 | 本文；对外统一用「业务 AgentOS（单节点）」，不再用「通用 AgentOS/多租户/应用生态」措辞 |
| Shell 边界 | 确认冻结范围：新功能暂停，仅保留 bugfix、可访问性与数据正确性修复（需拍板） |
| 词汇表 | 内核五原语、边界三态、能力合约、执行信封、投影层；与 MiniApp/团队既有文档对齐 |
| 验收 | 文档评审通过；不再新增与 Route A 冲突的承诺 |

### M1 · 治理内核（能力合约 / 边界三态 / 能力审计）

| # | Workstream | 做什么 | 验收 | 主要触点 |
|---|---|---|---|---|
| M1.1 | 能力合约注册表 | 为每个工具声明 `risk_level`、`reversible`、`money_threshold`、`external_commitment`、`requires_approval`、`scopes`；内置工具由平台标注，插件/技能由 manifest 声明；未标注默认拒绝 | 所有已挂载工具都能解析到合约；未标注工具被拒绝；合约可版本化 | `infra/agents/security/`、harness 工具执行钩子、插件/技能 manifest |
| M1.2 | 边界策略引擎 | 在工具执行前做三态判断：放行 / 推送审批 / 拒绝；审批复用 `hitl_pending` 与现有 resume 链路；拒绝返回结构化错误并记审计 | allow/approve/deny 三条用例通过；审批恢复后台账完整；目标 bounds 越界必须推送审批 | `infra/gateway/hitl/`、`infra/agents/teams/dispatcher.py`、工具执行钩子 |
| M1.3 | 能力调用审计 | Append-only 审计：actor、agent/team、tool、capability、decision、reason、参数摘要（不落 PII 原文）、结果、cost、trace_id；查询 API + 管理页；不提供删除 | 每次工具调用都有记录；可按 agent/team/时间查询；审计写入失败要显式失败或告警 | `infra/db/repos/audit.py`、新表/迁移、`api/routers/admin.py` |
| M1.4 | 目标 bounds 绑定 | `team_goals.bounds_json`：预算、触达范围、金额上限、必须审批的动作；Planner snapshot 带上 bounds，Dispatcher 校验 | 目标不能超出 bounds；越界计划在派发前被拦下 | `infra/agents/teams/decision.py`、`dispatcher.py`、迁移 |
| M1.5 | 解释 Worker（可并行） | 只读账本、无写工具、无调度权；回答「为什么没做/卡在哪/谁批的」 | 只能读本 agent/team 账本；账本无记录时明确回答「没有记录」 | `infra/agents/`、账本 repos、gateway 回复通道 |

**Gate 1（M1 完成）**：能回答「谁在何时用什么权限做了什么」；未标注能力默认拒绝；越界动作必须审批；目标不能超出 bounds。

### M2 · 执行内核（隔离信封 / 监督恢复 / 调度可靠性 / 预算硬闸门）

| # | Workstream | 做什么 | 验收 | 主要触点 |
|---|---|---|---|---|
| M2.1 | 执行信封 | 每 agent/app：并发上限、墙钟超时、最大工具调用数、token 硬顶、内存/CPU 保护；shell/execute 默认进隔离域（bwrap/子进程；Docker 可选）；取消要能强杀 | 失控循环在限定时间内终止；节点保持可响应；内存/CPU 超限可观测 | `infra/agents/manager.py`、`infra/backend/`、harness 执行层、`octop-harness` 配置 |
| M2.2 | 监督与恢复 | 持久化 turn/job 的 running/queued 状态；重启时标记孤儿并恢复或明确失败；`stop()` 取消在飞 self 回合 | 长任务中重启：状态可见、可恢复或明确失败；不再「重启即丢」 | `infra/agents/teams/jobs.py`、`inbox`、`cron/manager.py`、`proactive/scheduler.py` |
| M2.3 | 事件平面正确性 | 修 `docs/event-model.md` §5 的 P0：app inbox 幂等键按 app 作用域、PG 并发冲突安全、`app.import` 结果与 PII、`expert.turn` 幂等、限流可见、id 熵 | 每个 P0 有回归用例；重试不再重复跑 LLM | `infra/events/`、`infra/apps/service.py`、`infra/db/repos/` |
| M2.4 | 调度语义统一 | 一套 lease/idempotency/backpressure 抽象；cron/proactive/team 加 DB 租约或显式单实例断言；backlog/queue 指标 | 多实例下列举的任务不重复执行（或明确拒绝多实例）；积压可见 | `infra/cron/manager.py`、`infra/proactive/scheduler.py`、`infra/agents/teams/sweeper.py` |
| M2.5 | 预算硬闸门 | `paused_budget` 置位；`budget_reserved` 结算/释放；decision/proactive/cron/app assistant 全部计费；预算耗尽停止新工作 | 预算耗尽后不再派发；在途回合有上限；无未计费 LLM 路径 | `infra/agents/teams/dispatcher.py`、`team_runtime.py`、`usage_record.py`、`proactive/service.py` |

**Gate 2（M2 完成）**：一个失控 agent/长任务不能拖垮节点；重启后任务状态可见/可恢复；事件平面 P0 全绿；预算能硬停。

### M3 · 应用内核（包 v2 / 数据 P0 / 能力版本化）

| # | Workstream | 做什么 | 验收 | 主要触点 |
|---|---|---|---|---|
| M3.1 | 应用包 v2 | manifest（版本、能力版本、迁移、签名）、安装/升级/回滚、跨主机 Bundle v2；不带凭据/token | 应用可安装、升级、回滚、搬迁；目标机重新绑定 connector | `infra/apps/service.py`、`api/routers/apps.py`、备份/迁移 |
| M3.2 | 应用数据 P0 | 行级变更历史、引用列 + 聚合端点、查询下推、附件上传；行写入审计 | 改一行可查到「谁/何时/从什么改成什么」；2 万行筛选 ≤ 50ms；引用不存在被拒 | `infra/apps/models.py`、`service.py`、`db/repos/apps.py`、迁移 |
| M3.3 | 能力/API 版本化与服务端校验 | `CAPABILITIES_VERSION` 进 manifest/`me()`；弃用策略；高风险能力（ai/game/external/events/schedules）补服务端 `require_capability`；数据端点仍按用户 ACL | 旧应用在新宿主上行为可预测；绕过前端桥时高风险能力仍被服务端拦住 | `infra/apps/capabilities.py`、`api/routers/apps.py`、`MiniAppFrame/` |

**Gate 3（M3 完成）**：应用能安装/升级/回滚/搬迁；行级历史可举证；能力清单有版本与弃用策略。

### M4 · 可观测与核算（可从 M1 开始横向建设）

| # | Workstream | 做什么 | 验收 |
|---|---|---|---|
| M4.1 | trace / metrics / logs / readiness | 一次 turn 的 trace_id 贯穿 API→agent→tool→connector→LLM；指标导出（Prometheus/OTLP）；结构化日志；readiness 区分 DB/agent/harness | 一次 turn 可端到端追踪；积压/错误率/延迟可监控；readiness 能反映依赖状态 |
| M4.2 | 成本核算 | 按 agent / app / task / goal 归集 token 与金额；管理页；预算告警 | 任一应用/任务可查成本；超预算可告警/停派 |

**Gate 4（M4 完成）**：能回答「这次 turn 慢在哪、花了多少、卡在哪个队列」。

### M5 · 生态与联邦（条件触发）

| # | Workstream | 前置条件 |
|---|---|---|
| M5.1 | 市场、签名、审核、模板分发 | 至少 3 个外部团队在写应用；M3 完成 |
| M5.2 | 节点协调（复用 Bridge：注册、策略下发、审计汇总、任务重派） | 门店数 > 20 或明确跨店协同；仍坚持「每节点一个 Octop」 |

**不做**：分布式事务、会话级状态迁移、共享上下文；状态迁移退化为任务在中心重新分派。

---

## 4. 建议的第一个实施切片

不要一上来铺满所有工具。用**一个工具族**把治理闭环打通，再横向复制。

**切片 S1：M1.1 + M1.2 + M1.3，作用在 `app_*` 工具族**

1. 定义能力合约 schema 与内置工具标注（先覆盖 `app_*` 10-15 个工具）。
2. 在工具执行钩子上接入边界三态；危险动作走 `hitl_pending`。
3. 写 capability audit；提供按 agent/team/时间的查询。
4. 补行为测试：allow / approve / deny / 未标注默认拒绝 / 审批恢复。
5. 验证命令：
   - `uv run pytest tests/unit/apps tests/unit/agents -q`
   - `uv run pytest tests/integration/test_apps_api.py -q`
   - 合并前 `make all`。

**S1 的完成标准**：对话里让 agent 调用一个越界的 `app_*` 工具，必须弹出审批；拒绝后账本可查；未标注的新工具默认不可用。

---

## 5. 验收门槛（总表）

| Gate | 问题 | 通过标准 |
|---|---|---|
| G1 | 权限可追溯吗？ | 每次工具调用可查「谁/何时/什么能力/什么决定/结果」；未标注默认拒绝 |
| G2 | 会不会被一个 agent 拖垮？ | 失控循环/长任务/死循环应用不能拖垮节点；重启后任务可见 |
| G3 | 应用能搬家和升级吗？ | 安装/升级/回滚/跨主机迁移；行级历史可举证 |
| G4 | 能运营和算账吗？ | 端到端 trace、队列积压、每应用/任务成本可查 |

**在 G1、G2 通过之前，不建议对外使用「AgentOS 级隔离/生态/多租户」措辞。**

---

## 6. 风险与开放问题

### 6.1 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 能力合约标注负担 | 工具太多，标注不全；默认拒绝会打断现有使用 | 先覆盖高危工具族；提供 legacy 过渡模式（放行 + 审计 + 到期日） |
| 隔离机制选型 | bwrap/Docker/子进程各有平台与性能代价；Windows CI 不支持 bwrap | 定义「单用户可信模式」与「多用户默认隔离」两档；Windows 用子进程/作业对象兜底 |
| 审计写放大 | 工具调用频繁，同步写审计拖慢热路径 | 先异步批量写 + 失败告警；审计查询与业务库分离可选 |
| 预算与在途回合 | 硬停可能中断正在进行的 LLM turn | 新工作立即停；在途给上限与超时；结算 reserved |
| 包签名与审核 | 自签 vs 平台签影响生态信任 | M3 前只做自签/内容哈希；市场阶段再引入平台签 |
| Shell 冻结阻力 | 产品团队想继续加窗口功能 | 用 Gate 1/2 作为解锁条件；Shell 只做 bugfix 与状态服务端化 |

### 6.2 开放问题（需要拍板）

1. **多用户默认隔离级别**：默认 bwrap/子进程，还是要求 Docker？「单用户可信模式」是否允许跳过？
2. **能力合约归属**：内置工具由平台标注；插件/技能由作者声明还是管理员标注？未标注的第三方扩展是否一律拒绝？
3. **审计存储**：新表 `capability_audit`，还是扩 `audit_log`？保留期与不可变性要求？
4. **预算维度**：只做 token，还是同时做金额？限额由平台、团队 owner 还是管理员设置？
5. **应用包签名**：M3 只做内容哈希/自签，还是一开始就平台签？
6. **Shell 冻结范围**：新功能全停，还是只停窗口特效、保留状态服务端化？

---

## 7. 与现有文档的关系

| 文档 | 关系 |
|---|---|
| `docs/agent-os-overall-plan.md` | 设计记录；本文是它的「路线 A 实施顺序与验收」 |
| `docs/architecture-overview.md` | 现状与差距分类；本文更新了其中「控制面/隔离/可观测」的判断 |
| `docs/event-model.md` | M2.3 的缺陷清单来源 |
| `docs/miniapp-product-plan.md` | M3.2 的 P0 来源；其非目标继续有效 |
| `docs/digital-employee-plan.md` | M2.2/M2.5 的自主执行缺口来源 |
| `docs/team-l3-roadmap.md` | L3 目标与本文不冲突；本文把「队列/背压/租约」纳入执行内核 |
| `docs/adr/001-single-process-model.md` | 单进程约束继续有效；本文不把内核改成分布式 |
| `docs/adr/002-database-backends.md` | 单写者承诺继续有效；多租户不在本路线内 |
