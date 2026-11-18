# 业务 AgentOS 状态复盘与必做清单

Status: status review / decision record
Date: 2026-11-16
Scope: Route A（单节点业务 AgentOS）的当前状态、问题、必做功能与阶段计划。
Related: `docs/agent-os-roadmap.md`（实施路线）、`docs/agent-os-overall-plan.md`（设计记录）、
`docs/architecture-overview.md`（现状）、`docs/event-model.md`（事件保证与缺口）。

---

## 0. 结论

**当前形态**：Octop 是一个成熟的「单节点 Agent 应用平台」，加上一个完成度很高的桌面 Shell；
还不是 AgentOS。通知持久化与窗口休眠属于 Shell/可靠性层，不能替代内核。

**最大风险不是功能少，而是两件事**：

1. **内核四件套缺位**：执行隔离、能力授权、统一调度、可观测/核算。
2. **交付基线失控**：当前分支工作区有 **152 个改动文件**（93 staged、25 untracked），
   全量前端测试有 9 个既有失败，`make all` 的 5900 项 pytest 在 2 核机器上无法在合理时间跑完。

**结论**：先止血交付基线，再做 7 项「不做不可」；其余功能全部排在 Gate 之后。

---

## 1. 当前状态

### 1.1 已完成（本轮及分支既有）

| 层 | 状态 |
|---|---|
| 通知/审批持久化 | 新增 `notifications`（schema v39）、服务端 inbox、4 个 API、`notification*` WS 帧；访问申请、cron HITL、聊天历史 HITL 落库；通知中心合并服务端与本地临时通知 |
| 隐藏窗口资源治理 | 最小化/跨空间暂停 5 秒 `data_version` 轮询；隐藏 30 分钟卸载 MiniApp/外链 iframe；专家会话窗口不卸载 |
| MiniApp | R1 + R2.0–R2.3：数据/视图/发布/能力清单/事件收件箱/出站 Webhook/应用级调度 |
| Agent/团队 | Agent 运行时、团队目标/任务/派发/审批/预算（token）、bounded-agentic 编排 |
| 桌面 Shell | 窗口注册表、拖拽/缩放/层级、Dock、Mission Control、命令面板、通知中心 |
| 运维 | setup、TLS、backup、systemd、i18n、多 IM/连接器 |

### 1.2 内核五原语

| 原语 | 现状 | 判定 |
|---|---|---|
| 目标入口 | `team_goals` + autonomy API；bounds 未绑定 | ✅ / ⚠️ |
| 执行运行时 | `octop-harness` + `AgentManager`；单进程、可选 Docker | ✅ |
| 能力合约注册表 | 只有工具级 `SecurityPolicy` | ⚠️ |
| 边界策略引擎 | 无「放行 / 审批 / 拒绝」三态 | ❌ |
| 审计链 | `audit_log` 只覆盖管理动作；无工具调用审计 | ⚠️ |

### 1.3 交付基线

| 项 | 数字/状态 |
|---|---|
| 分支 | `feature/miniapp-capabilities` |
| 工作区 | 152 个改动文件；93 staged；25 untracked |
| schema | v39（`notifications`） |
| 后端定向验证 | 通知/i18n 95 passed；db/backup/gateway/api 1160 passed；apps API 64 passed；gateway+db_pool 587 passed |
| 前端定向验证 | 59 passed；`tsc -b` exit 0 |
| 静态检查 | `make lint`、`make typecheck`（681 files）通过 |
| 全量 `make all` | format/lint/typecheck 通过；pytest 5900 项，2 核 3 分钟仅 37 项，已停止 |
| 全量前端 | 1639 项；当时 10 失败，其中 1 项由本轮引起并已修复；其余 9 项为既有 mock/断言漂移 |
| 已知既有失败 | AuthGuard、DocumentPreview docx、expertColor、knowledgeBases、publishedExperts、moduleClasses、skillMarkdown、ChatInput.prefill、SkillDrawer |

---

## 2. 反思：问题

### 2.1 战略/定位

- **叙事超前于内核**：桌面 Shell 已经像 OS，但执行隔离、权限、调度、核算仍是「Web 平台」。
  对外继续用「OS 级隔离/生态/多租户」会兑现不了。
- **单节点是正确取舍**：边缘盒子/每客户一实例成立；不要为了「通用 AgentOS」改造分布式内核。
- **功能惯性**：Shell/应用层容易继续加，内核层反馈慢；需要明确的冻结与 Gate。

### 2.2 架构

| # | 问题 | 后果 |
|---|---|---|
| D1 | 执行隔离为零：默认 host-rooted `local_shell`，Agent 是进程内对象；无 per-agent 并发/内存/墙钟配额 | 一个失控循环拖垮节点 |
| D2 | 权限是用户 ACL + 前端能力位：无 app principal，服务端只对 3 类能力二次校验，工具调用无审计 | 无法最小权限、追责、抗注入 |
| D3 | 调度/事件语义碎片化：五套循环；cron/proactive/team 无 DB 租约；事件平面仍有 PG 并发/expert.turn 重试缺口（app inbox 作用域与 delivery 结果已修 v41） | 重复执行、丢事件、不可证 |
| D4 | 预算滞后：只有团队 token 预算 opt-in；无 `paused_budget`、reserved 结算、全路径计费 | 账单/死循环保护不足 |
| D5 | 应用不是包：只有当前 HTML + 一版回滚；无版本/签名/迁移/市场；9 种扩展面、两种信任模型 | 无生态、升级/搬迁困难 |
| D6 | 控制面/数据面同库、单写者、无 tenant；app 行 document-in-row、2 万行上限、全表过滤 | 热点争用、无租户、无扩展 |
| D7 | 可观测/审计最低配：进程内计数、文本日志、health 恒 ok；工具/行写入无审计 | 无法运营、取证、算账 |

### 2.3 产品/体验

- 审批处理完后通知不会自动已读；聊天回复提示仍是客户端临时通知。
- 窗口布局仍只存 localStorage，换设备/换浏览器即丢；多设备承诺不成立。
- MiniApp 行级变更历史已完成（v40，2026-11-17）；仍缺引用/聚合、查询下推、附件上传：业务审核场景成立，报表场景仍不成立。
- 没有解释 Worker：老板问「为什么没做/卡在哪/谁批的」只能翻数据库。
- 自主执行有断点：goal-only 不唤醒拆解；self-execute HITL 未接线；`stop()` 不取消在飞回合。
- 团队缺独立验收/证据/绩效；成本只有 token 维度，无金额与降级。

### 2.4 工程/流程

- **152 个未提交改动**：review 不可做、回归不可见、回退困难、合并冲突风险高。
- **全量测试不可本地执行**：5900 项 pytest 在 2 核机器上不可行；CI 是唯一可信门禁。
- **既有前端失败**：9 项 mock/断言漂移说明 CI 或分支基线未强制全绿。
- **文档漂移**：多处版本号/状态描述滞后；本轮只修了直接相关的部分。
- **迁移耦合**：schema 版本一升，多个测试/备份断言需要同步；需要迁移 checklist。

### 2.5 安全

- 默认 Agent backend 是 host-rooted `local_shell`；Docker/bwrap 是可选，不是默认。
- 插件是进程内 Python（`pip install` + `exec_module`），与 MiniApp 的「不可信沙箱」是两套信任模型。
- 能力清单主要在前端桥执行；绕过桥时服务端只靠用户 ACL。
- secrets 加密 key 与控制面同库；备份是未加密 tar.gz；拿到备份/DB 即拿到凭据。

---

## 3. 必做（不做不可）

> 判据：缺了它就不能称为「业务 AgentOS」，或者会直接造成数据/账单/安全事故。
> 其余功能一律排在 Gate 之后。

| # | 必做功能 | 为什么不做不可 | 验收标准 |
|---|---|---|---|
| **M1** | **能力合约 + 边界三态 + 能力审计**（含目标 bounds） | 没有它，就没有最小权限、审批、追责；这是 Route A 的差异化 | 未标注工具默认拒绝；越界 `app_*` 调用弹审批；每次工具调用可查「谁/何时/什么权限/结果」；目标不能超 bounds |
| **M2** | **执行信封 + 监督恢复** | 一个失控 agent/长任务现在能拖垮全节点；重启即丢 | 失控循环被限时终止；节点保持可响应；重启后 in-flight 任务可见/可恢复或明确失败；`stop()` 取消在飞回合 |
| **M3** | **事件平面 P0 + 统一调度** | 重复执行/丢事件/语义不一致会让自动化不可信 | `docs/event-model.md` §5 P0 全绿（app inbox 作用域 + delivery 结果已修 v41；PG 并发/expert.turn 重试待修）；cron/proactive/team 有租约或显式单实例断言；backlog/背压可见 |
| **M4** | **预算硬闸门** | 账单失控与死循环是业务底线 | `paused_budget` 生效；reserved 结算/释放；decision/proactive/cron/assistant 全部计费；可设金额维度与降级 |
| **M5** | **应用数据 P0** | 行级历史已完成；没有引用/查询下推，报表场景仍不成立 | 改一行可查「谁/何时/从什么改成什么」（✅ v40）；引用不存在被拒；2 万行筛选 ≤50ms；附件可上传 |
| **M6** | **可观测/核算** | 无法定位慢/错/贵，就无法运营 OS | 一次 turn 可端到端追踪；指标可导出；每 agent/app/task 成本可查；readiness 能反映 DB/agent/harness |
| **M0** | **交付基线** | 152 文件 WIP + 红色全量测试，任何功能都不可信 | 工作区按主题切分提交/PR；CI 全量绿；9 个既有前端失败修复或建单隔离；迁移/备份断言同步 |

**已完成、继续保持**：通知/审批持久化、隐藏窗口休眠、MiniApp 能力清单/双层沙箱、多用户 ACL、事件 outbox、桌面 Shell 不新增后端权限。

---

## 4. 计划

> 周期按 2–3 人全职估算；先做 M0，否则后面所有验证都不可信。

### Phase 0 · 交付止血（约 1 周）

1. 把 152 个改动按主题切分：MiniApp R2.3 / 通知收件箱 / 窗口休眠 / 文档；分别提交与 PR。
2. 在 CI 或更强机器跑一次全量 pytest + vitest，记录基线。
3. 修复或建单隔离 9 个既有前端失败；补 `make all` 的 CI 门禁。
4. 通知自动已读（HITL resume / 访问申请处理后）；文档版本号校准。
5. **冻结 Shell 新功能**（仅 bugfix、可访问性、数据正确性）。

**出口**：CI 全绿或失败项全部有单；本轮功能可 review/回退。

### Phase 1 · 治理内核（约 2–4 周）→ Gate 1

- M1：能力合约 schema（先 `app_*` 工具族）→ 边界三态 → 能力审计 → 目标 bounds。
- 审批复用 `hitl_pending`；未标注默认拒绝，旧工具「放行 + 审计 + 到期日」过渡。
- 解释 Worker 可并行（只读账本，无写工具）。

**出口（Gate 1）**：能回答「谁在何时用什么权限做了什么」；越界必须审批；目标不能超 bounds。

### Phase 2 · 执行内核（约 4–6 周）→ Gate 2

- M2：执行信封（并发/墙钟/工具数/内存/CPU/隔离）+ 监督恢复 + 取消。
- M3：事件 P0 修复 + 统一租约/背压/backlog。
- M4：预算硬闸门 + 全路径计费。

**出口（Gate 2）**：失控 agent 不能拖垮节点；重启后任务可见；事件 P0 全绿；预算能硬停。

### Phase 3 · 业务数据与应用（约 4–6 周）→ Gate 3

- M5：行级历史、引用/聚合、查询下推、附件上传。
- 应用包 v2：manifest/version/migration/signature/跨主机迁移。
- 能力/API 版本化 + 高风险能力服务端校验。
- Shell 可并行：窗口布局服务端化、通知偏好、深链接。

**出口（Gate 3）**：应用可安装/升级/回滚/搬迁；行级历史可举证；2 万行筛选 ≤50ms。

### Phase 4 · 可观测与核算（约 2–3 周）→ Gate 4

- M6：trace、metrics 导出、结构化日志、readiness、审计查询。
- 成本：按 agent/app/task/goal 归集 token 与金额；预算告警。

**出口（Gate 4）**：一次 turn 能回答「慢在哪、花了多少、卡在哪个队列」。

### Phase 5 · 生态/联邦（条件触发）

- 应用市场/签名/审核；节点协调（复用 Bridge，每节点一个 Octop）。
- 只有 M3 完成 + 至少 3 个外部团队/门店数 >20 才启动。

---

## 5. 现在不做（明确冻结）

| 不做 | 原因 |
|---|---|
| 窗口吸附/平铺、Mission Control 缩略图、多选/分组、启动动画/桌宠特效 | 展示层长尾，不解决业务问题 |
| 通知偏好、自定义空间 | 等通知/空间有真实使用数据 |
| 多租户 SaaS、多节点集群、分布式内核改造 | 与 ADR 001/002 冲突；每节点一实例是正确形态 |
| 通用第三方代码运行时、原生小程序 | 现有 MiniApp 定位是零后端投影层 |
| 通用 EventBus、第二套投递引擎 | 五类事件契约已够用 |
| 应用市场/签名/审核、移动端 Shell、离线/PWA | 等 Gate 3/4 后再评估 |
| 为了让 Shell 更像 OS 而继续加窗口功能 | 内核未完成前收益递减 |

---

## 6. 立即可执行的 5 件事

1. **切分并提交当前 WIP**：先通知收件箱 + 窗口休眠，再 MiniApp R2.3，再文档；每项独立 PR。
2. **建立 CI 全量基线**：修 9 个既有前端失败，或建单隔离并让 `make all` 成为合并门禁。
3. **通知自动已读**：HITL resume / 访问申请处理后按 `dedupe_key` 标记已读。
4. **启动 S1**：`app_*` 工具族的能力合约 → 边界三态 → 能力审计。
5. **写一张冻结清单**：Shell 新功能、应用市场、多租户、分布式，全部等 Gate。

---

## 7. 开放问题（需拍板）

1. 未标注工具的过渡策略：默认拒绝 vs 放行+审计+到期日（建议后者）。
2. 能力审计存储：新 `capability_audit` 表 vs 扩 `audit_log`（建议新表，append-only）。
3. 多用户默认隔离：bwrap/子进程 vs Docker；是否允许「单用户可信模式」跳过。
4. 预算维度：token 还是金额；限额由平台/团队 owner/管理员设置。
5. 全量 CI 机器与门禁：本地 2 核不再承担 5900 项。
