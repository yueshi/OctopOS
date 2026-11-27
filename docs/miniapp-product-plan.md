# MiniApp 产品定位与路线图

> 日期：2026-11-15（事实数据同步至 2026-11-24），分支 `feature/miniapp-capabilities`，schema **v34** → 现状 **v52**。
>
> 口径：**实现现状以 [MiniApp 功能与架构现状](./miniapp-overview.md) 为准；需求与决策历史见
> [MiniApp 平台路线图](./miniapp-platform-roadmap.md)；阶段执行细节见
> [MiniApp 实施落地计划](./miniapp-implementation-plan.md)。本文只回答两个问题：
> **这个产品为谁而做、不做什么；以及按什么顺序把它做完。**
>
> **现状更新（2026-11-18）：** 页面写能力已补齐：`updateRow` / `updateRows` / `deleteRow`
> 已于 2026-11-15 落地（行级 CAS、批量逐行提交），行查询 `filter` / `sort` 与
> `data_version` 轮询也已支持。本文 §2 中“当前缺口：页面只能新增”等段落是撰写时的
> 历史快照，**不要作为当前能力依据**；当前口径以 overview 与代码为准。

---

## 1. 定位

### 1.1 一句话

**MiniApp = 平台内置的「零后端轻应用运行时」：把一份结构化数据，变成一个能被人直接使用、
能嵌进任何宿主、能被 Agent 生成和更新的小界面。**

三个设计支柱：

| 支柱 | 含义 | 守住了什么 |
|---|---|---|
| **零后端** | 表、行、权限、审计、发布、备份都由平台提供；小应用不需要自己的数据库和 API | 集成成本 = 写一份 HTML + 一张表 |
| **可嵌入** | 沙箱 iframe + `octopApp` SDK + 能力清单，是平台唯一的「小应用协议」 | 看板格子 / 聊天侧栏 / 桌面窗口 / 公开页共用一套渲染与权限 |
| **受控** | 页面无 JWT、无网络；外部 I/O 全部宿主代调；能力先声明后使用 | 应用可对外分享而不泄露凭据 |

### 1.2 明确的非目标

这些不是「还没做」，是**决定不做**。它们同时是路线图里的取舍依据。

| 非目标 | 理由 |
|---|---|
| 不是业务流程引擎 | 审批/编排属于 Octop 的 `infra/cron`、`infra/events`、Teams；MiniApp 只承载结果（overview §1.2） |
| 不是 BI / 报表工具 | 无跨表 join、无钻取、无复杂筛选；表格定位是投影层，不是权威业务库（overview §1.2） |
| 不是通用富媒体运行时 | 素材仍以图片为主；视频只支持**平台托管的同源资源**（阶段 1.5，限定路径与来源），不做任意 URL、不做页面内上传大文件、不做逐帧剪辑 |
| 不是多页面前端 | 一个应用一份 HTML（≤2MB），无路由；多视图靠页面内 JS 切 tab |
| 不是低代码平台 | 不提供任意服务端 JS；声明式规则（校验/枚举/状态机）已足够，计算字段与触发器延后（roadmap §2 决策 10） |
| 不是原生小程序运行时 | 没有每应用进程、没有服务端执行页面代码、没有自定义域名（overview §1.2） |
| 不是权威业务数据源 | 任务实体仍落原生表；MiniApp 是展示与装配层（overview §1.2） |

### 1.3 与相邻形态的分工

同一个团队很容易把一件事做两遍。边界如下：

| 形态 | 用它做什么 | 什么不做 |
|---|---|---|
| **MiniApp** | 单表/少表数据的界面；轻量填报；轻看板；**审核与审片**；聊天内摘要卡 | 多步流程、逐帧时间轴/剪辑、重聚合、多页 |
| **插件 UI**（`plugin.yaml` 的 `ui.entry`） | 嵌在 Dashboard 聊天流里的业务卡片；随插件分发的界面 | 多页应用；沙箱外的前端逻辑 |
| **React 页面**（平台外或平台内正式前端） | 多页应用；逐帧时间轴/多轨剪辑；重 BI 钻取 | 一切需要免登录嵌入的场景 |
| **原生表 + `infra/cron` / `infra/events`** | 权威业务数据；定时任务；跨系统编排 | 面向人的交互界面 |
| **聊天卡片（`octop_ui` renderer）** | 对话流里的即时结果 | 需要持续状态的应用 |

> 判据：**要「嵌到别处、免登录、单表或少表、读多写少」→ MiniApp；要「多页、要逐帧剪辑、要重交互」→ React；
> 要「权威数据与编排」→ 原生表 + cron/events。**
>
> 「读多写少」是 MiniApp 的典型使用形态，不是能力限制；页面写能力
> （`updateRow` / `updateRows` / `deleteRow`）已于 2026-11-15 补齐。

### 1.4 MCP Apps：分发面，不是地基

> 记录于 2026-11-15。**结论：MCP Apps 能替代界面与沙箱层，替代不了数据与治理层 —— 而地基由后者定义。**

#### 它解决什么 / 不解决什么

| MiniApp 的短板 | MCP Apps 能否解决 |
|---|---|
| 沙箱与 dashboard 同 renderer process | ✅ 规范强制 UI 走**不同 origin 的 sandbox proxy**，比现在的 `srcdoc` + wrapper 更强 |
| opaque origin 导致 CSP 必须写死 host origin | ✅ 资源走 proxy origin，不必放宽宿主 CSP |
| 页面 → 宿主 → 后端的数据通道 | ✅ 标准 JSON-RPC 桥 |
| 行级变更历史、存储收口、查询下推、引用与聚合、统一身份与 ACL | ❌ **全部不解决** |

MCP Apps 换的是**界面层**；而 §3.3 的六个 P0 里，五个在数据层与治理层。

#### 宿主覆盖（2026-11 检索，未能直连官方矩阵核对）

官方扩展标识 `io.modelcontextprotocol/ui`，**双向 opt-in**（宿主与 server 都要声明）。

| 宿主 | 状态 |
|---|---|
| Claude（web / Desktop / iOS / Android） | ✅ 首发宿主 |
| ChatGPT（web，Business/Enterprise/Edu dev mode） | ✅ |
| Cursor | ✅ v2.6 起 |
| **腾讯 WorkBuddy 企业版** | ✅ 有官方「MCP Apps 接入指南」；见下 |
| VS Code + GitHub Copilot | 🟡 实验性，需开 `chat.mcp.apps.enabled`，仅 inline |
| Microsoft 365 Copilot | 🟡 部分支持 |
| Goose（Block） | 🟡 实验 / 最小实现 |
| Postman / MCPJam / MCP Inspector / Archestra.AI / PostHog Code | ✅ 主要作调试与验证宿主 |
| **Claude Code / Codex CLI** | ❌ **不支持** —— 纯终端，只渲染工具文本；每个 tool 必须带文本兜底 |
| Gemini CLI / Devin Desktop | ❔ 未文档化 |
| LibreChat | 🟡 仅旧版 MCP-UI |

> **验证状态**：官方矩阵 `modelcontextprotocol.io/extensions/client-matrix` 在本机网络策略下取不到；
> 上表来自检索摘要交叉印证。**上线前以官方矩阵 + 目标宿主实测为准，并记录版本号与日期。**

#### WorkBuddy（腾讯）的具体约束

官方文档要点（`tencentcloud.com/zh/document/product/1316/84439`、`cloud.tencent.com.cn/document/product/1831/137044`）：

- UI 资源：`ui://` 前缀 + MIME `text/html;profile=mcp-app`；CSP/权限写在资源 `_meta.ui`；
- tool 用 `_meta.ui.resourceUri` 绑定 widget；调用时宿主渲染并把 `structuredContent` 推给 widget；
- 运行时：跨 origin sandbox iframe + 宿主提供的 sandbox proxy 页，HTML 经 `postMessage` 注入，
  通信走 `@modelcontextprotocol/ext-apps`；
- **反向 `tools/call` 每次弹用户授权框**（拒绝返回 `{isError:true}` 且不触达 server）；
- **HTML > 256 KB 时降级为只传 URI**，由 widget 自己去 fetch 资源。

**三个直接冲突：**

1. **256 KB vs 2 MB**：MiniApp HTML 上限 2 MB，超过 256 KB 在 WorkBuddy 里会多一次 widget 侧资源拉取
   → **`ui://` 资源必须能被匿名或带短时 token 访问**；而 `GET /api/apps/{id}/html` 现在要 JWT
   （与阶段 6 的「平台级 capability token」是同一件事）。
2. **授权语义重叠**：WorkBuddy 对反向调用强制弹窗，与 MiniApp 的 `data.write` 能力位不是一回事。
   建议明确：**宿主弹窗 = 用户最终确认；能力位 = server 侧最小权限**，两者叠加而非互替。
3. **终端不可用**：Claude Code / 终端模式拿不到 UI，只能读文本兜底。

#### 建议做法：做「门面」，不做「重构」

| | 打包成 MCP server（建议） | 改造成 MCP Apps 客户端（不建议） |
|---|---|---|
| 改什么 | 加一层门面：表的读写暴露为 MCP tools，`app.html` 暴露为 `ui://` 资源 | 重画数据模型（应用 = MCP server + 资源，而非「一份 HTML + 表」） |
| 数据模型 | **不变** | 变，要迁移 `light_app_rows` 语义 |
| 收益 | 同一应用能被 Claude / ChatGPT / Cursor / WorkBuddy 消费；Octop 自己继续用 iframe + wrapper | 界面层更标准 |
| 风险 | 中：复用现有 `internal_mcp.py` 的 initialize / tools 骨架 | 高：要自实现完整 host 侧（proxy、消息校验、资源路由），且生态仍早 |

**顺序：先按 §3.3 打完 P0 六项（别人替不了），再把 MiniApp 打包成 MCP server 出去（MCP 能给你）。**
顺序反了就是为标准化而重画数据模型 —— 最贵的一笔开销，且换不来任何地基能力。

---

## 2. 目标用户与四个高频场景

MiniApp 服务三类人（做台账的、看数的、审内容的），场景收敛到四个。其余需求一律按 §1.2 拒绝或转介。

### 场景 A：台账与填报（多人协作）

- **谁**：业务同事、门店/班组负责人、运营助理。
- **怎么做**：Excel 快速模式建表（确定性、不打 LLM）→ 配 `row_scope=user` → 配状态机规则 →
  页面做填报/筛选/汇总 → 按人可见。
- **原缺口（已补齐）**：页面写能力（`updateRow` / `updateRows` / `deleteRow`，2026-11-15）、
  行查询 `filter` / `sort`、`data_version` 轮询（2026-11-15）均已落地；
  当前按 `row_scope=user` + 状态机设计多人填报即可。
- **判定**：**这是 MiniApp 最该吃下的场景**。

### 场景 B：轻看板与摘要卡（单人或小团队）

- **谁**：管理者、看数的人；以及需要把「一次生成的结果」变成可点对象的所有人。
- **怎么做**：Excel/CSV → 快速模式出 `dashboard`/`ledger`/`board` 视图；或 Agent 用
  `app_create` + `app_write_html` 写；发布成只读公开链接；挂到工作台 Board 格子或聊天卡片。
- **当前缺口**：Agent 助手图表只采样前 500 行；快速模式看板只支持柱状与折线两种自绘图形。
- **判定**：**已具备可用能力**，优先补「结果落地」而非图形种类。

### 场景 C：外部数据速览（依赖 R2.1）

- **谁**：需要把外部系统/接口的数据随手看一眼的人（读书记录台这类）。
- **怎么做**：R2.1 的外部服务绑定 → 页面经宿主调用只读连接器工具 → 缓存 → 展示。
- **当前缺口**：`light_app_services` 未实现，页面只能读自己的表。
- **判定**：**这是 R2.1 的真正用例**，也是 MiniApp 从「静态看板」变成「活应用」的分水岭。

### 场景 D：审核与审片（业务内容审核）

- **谁**：审片人、质检、合规；以及需要「看内容 → 判断 → 留痕」的所有人。
- **怎么做**：表 + 状态机规则（`state_field` + `transitions`）承载审核流转 → 页面做列表 +
  内容预览 + 通过/驳回 + 意见 → 导出审核记录 → 挂到工作台；Agent（质检 Agent）直接读写同一张表。
- **当前状态**：页面写能力已补齐（`updateRow` / `updateRows` / `deleteRow`，2026-11-15），
  状态机跃迁可由页面驱动；行查询已支持 `filter` / `sort`。剩余缺口是**页面不能上传文件**、
  **页面放不了视频**（CSP `media-src data: blob:`；写时扫描不拦相对 URL，
  `src/octop/infra/apps/models.py:620-625`，唯一关卡在运行时 CSP）。
- **判定**：**数据模型天然契合**（状态机、行级范围、ACL、审计、导出、嵌入全部现成），
  差的只是「写」与「媒体」两组接口 —— 补齐后 MiniApp 能吃下整类「内容 + 状态」应用
  （审片台、课件预览、商品视频、培训录像回看、样片审阅）。
- **边界**：审核 ≠ 剪辑。逐帧时间轴、多轨、拖拽留在 React 侧（见 §1.3）。

---

## 3. 现状盘点

### 3.1 已落地（R1 + R2.0）

| 域 | 状态 | 关键事实 |
|---|---|---|
| 数据底座 | ✅ | 多表（≤12）、行级 CAS、幂等、懒迁移、导入/导出、备份/恢复 |
| 创建 | ✅ | Agent 工具、Excel/CSV 快速模式（确定性、不打 LLM）、8 个系统模板 + 专家模板 |
| 界面 | ✅ | 单文件 HTML、`window.octopApp`（23 方法）、能力清单（14 位）、双层沙箱 + CSP + WebRTC 冻结 |
| 协作 | ✅ | view / edit_data / edit_structure、协作者（≤50）、应用交接、删除守卫 |
| 发布 | ✅ | 只读公开链接、发布快照、公开表可见性、PII 脱敏、限流、撤销、上一版回滚 |
| AI | ✅ | 应用内只读数据助手（含图表）、一次性文本生成，计入 owner 额度 |
| 数据源 | ✅ | 工作区文件 + 只读连接器工具；手动/定时、游标增量、upsert、失败退避 |
| AI 任务 | ✅ | `light_app_tasks`（v50）：`summarize_row` / `table_digest` / `pre_review`、任务 API/SDK、4 个 Agent 工具、每日 token 上限、完成通知 |
| 外部写服务（V1-B） | ✅ | `provider=extension`（v52）：读写工具契约、幂等账本、两阶段确认、`submission_unknown` 对账、50 MB 分片上传、媒体 Range 代理、动态 CSP |
| 内置数据集 | ✅ | `builtin.dataset` 只读契约（describe/query）、`light_app_bindings`（v32） |
| 行级范围 | ✅ | `row_scope` = `app` / `user`（v33），读/写/导出/公开面均生效 |
| 声明式规则 | ✅ | 状态机（`state_field` + `transitions`）（v34） |
| 工作台 | ✅ | 个人多 Board、12 列网格、缩放/锁定/撤销/全屏/自动排列 |
| 游戏 | ✅ | 白名单外部游戏、一次性会话 token、结果回写、侧栏运行 |
| IM | ✅ | `app_share_link` 一次调用交付、纯文本格式化、单轮预算；owner 绑定后返回 app-scoped 免密链接 |
| 运维 | ✅ | 指标、审计、限流（每进程） |

规模：42 个 `app_*` Agent 工具；约 156 个 MiniApp 相关 HTTP 路由（含 1 个公开宿主页）；后端 `infra/apps/` 39 个模块、约 2.36 万行。

### 3.2 未落地

| 项 | 归属 | 备注 |
|---|---|---|
| 上游合并 + 迁移重编号 + 桥接迁移 | **阻塞项** | 本分支迁移 025–034（`025_light_apps` 起）与上游已发布的 `025_user_mfa` 冲突；旧水位库会跳过上游 022–025，不会自动补齐 |
| 专家可见性统一到上游分组模型（`GroupAccess`） | **阻塞项** | 分组受限专家的可见性判断当前靠 `is_shared` |
| 权限语义收口（`edit_schema` / `manage` 拆分） | 高 | 需一次 API 破坏性变更的决策 |
| 访问申请：owner 分裂 + `pending` 唯一索引 | 已完成（2026-11-15，v35） | 双 owner 通知/两步批准；partial unique index |
| R2.1 外部服务 `light_app_services` | 部分完成（v36） | 声明/调用/缓存/Agent 工具/图片资产/公开缓存已落地；promote 待做 |
| R2.2 事件收件箱 / `events.emit` | 完成（v37 + v42） | 收件箱/游标/ack/SDK/触发器/app.import 幂等已落地；出站事件复用 `infra/events` outbox + HMAC webhook |
| R2.3 应用级调度 `light_app_schedules` | 中 | 见 §4 阶段 4（只做两个动作） |
| R2.3 最小 workflow runner | 低（建议不做） | 见 §6 取舍 3 |
| R2.4 公开看板 | 中（可滑档） | 见 §4 阶段 5 |
| R3 Bundle v2（跨主机迁移） | 高 | 见 §4 阶段 6（建议提前） |
| R3 IM 认证链接 / 渠道身份 | 中 | D4-lite 已落地；PR-11/12 的 sender 身份、app 工具收口与 `/link` 已实现并部署（schema v48） |
| `data_version` 实时刷新 | **已完成（2026-11-15）** | `GET /api/apps/{id}/data-version` + 5 秒两段式轮询 + `data-changed` 事件（`dashboard/src/components/MiniAppFrame/useMiniAppDataVersion.ts`） |
| 页面写能力（`updateRow` / 批量写） | **已完成（2026-11-15）** | SDK + 双宿主 bridge + 行级 CAS；见 §3.3 V1 |
| 行查询 `filter` / `sort` | **已完成（2026-11-15）** | `GET /rows?filter=&sort=`；未下推，见 §3.3 P0-4 |
| 页面上传通道 | 中 | 见 §4 阶段 1、§3.3 P0-5；需 owner 授权 + 白名单 |
| 同源媒体端点 + CSP `media-src` 放开 + 媒体令牌 | 中 | 见 §4 阶段 1.5 |
| 行级变更历史 | **已完成（2026-11-17，v40）** | `light_app_row_history` + update/delete/replace 写入 + `GET /api/apps/{id}/rows/{row_id}/history`；见 §3.3 P0-1 |
| 引用列 / 跨表聚合 | **已完成（2026-11-18）** | `reference` 列（`ref_table` / `ref_label`）+ 写入校验 + 删除/替换守卫 + `expand_refs` label 展开 + 备份 v2 行 id 重映射；无多对多/级联；见 §3.3 P0-2 |
| 存储与派生副本收口 | **已完成（2026-11-17）** | v2 不再写空 CSV；manifest 去掉 `row_count`；写后统一走 `_after_write`；见 §3.3 P0-3 / §5 |
| 能力位版本化（`CAPABILITIES_VERSION`） | 中 | 页面契约演进依据；见 §3.3 P1-7 |
| 按应用选择模型 | 低 | 目前用 owning agent 默认模型 |

### 3.3 地基 MVP：验收线与缺口

> 本节回答一个问题：**MiniApp 什么时候才算"够用的地基"**。
> 不用功能数量衡量，用"能不能撑住三类真实工作"衡量。路线图（§4）服务于这里的验收线；
> 与验收线无关的阶段可以滑档。
>
> 进度快照（2026-11-18）：页面写、行查询 filter/sort、`data_version` 两段式轮询、
> 行级变更历史（v40）、存储与派生副本收口、服务端分组聚合、引用列已落地；
> 查询下推、上传通道仍未落地。

#### 验收线

| # | 验收场景 | 走通的标准 | 当前 |
|---|---|---|---|
| **V1 台账填报** | 多人往一张表里填 / 改 / 筛 | 新建 → 修改 → 按状态筛选 → 导出，全程页面内完成 | ✅ 基本达标 |
| **V2 审核留痕** | 看内容 → 判定 → 留痕 → 可追溯 | 谁在何时把哪条从 A 改成 B，事后可查、可举证 | ✅ 已达标（2026-11-17，v40） |
| **V3 单主题报表** | 少量关联数据的汇总视图 | 不写后端，靠表内聚合 + 引用 label 出数 | ✅ 已达标（2026-11-18）：聚合 + 引用列 |

#### P0 · 阻断验收

| # | 缺口 | 现状与证据 | 最小形态 | 规模 | 验收条件 |
|---|---|---|---|---|---|
| **1** | ~~**行级变更历史**~~ **已完成（2026-11-17，v40）** | `light_app_row_history` 记录 `update` / `delete` / `replace` 的 before/after、actor、source；`import_rows` 的 upsert 也写入；`GET /api/apps/{id}/rows/{row_id}/history` 按视图脱敏 PII；行历史由应用调度器按 90 天清理；调度数据源的 replace/upsert 同步不写历史，避免 O(rows)/次放大 | 已交付 | 已完成 | 改一行后能查到「谁 / 何时 / 从什么改成什么」；替换导入能定位被覆盖的行 |
| **2** | ~~**引用完整性最小集**~~ **已完成（2026-11-18）** | `reference` 列（`ref_table` / `ref_label`）存目标 `row_id`；写入/导入校验目标行存在；被引用行删除与 replace 同步被拒（`APP_ROW_IN_USE`）；`GET /rows?expand_refs=true` 返回 label；备份 v2 用 `row_ids.json` 保留关系并在恢复时重映射；聚合此前已交付 | 已交付：**不做级联、不做多对多** | 已完成 | 引用不存在的目标行被拒；删除被引用行返回明确错误；页面/Agent 能拿到 label；备份恢复后引用仍有效 |
| **3** | ~~**存储与副本收口**~~ **已完成（2026-11-17）** | v2 create/create_table 不再写空 `data.csv`；manifest 去掉 `row_count`；所有写后 manifest 刷新收敛到 `_after_write`；计数/`data_version` 仍由 repo 在同一事务内维护 | 已交付 | 已完成 | 工作区不再存在误导性数据文件；任一写路径后计数与 manifest 一致 |
| **4** | **查询下推 A′** | 过滤 / 排序全表取回后 Python 处理；实测 **16 ms/千行，2 万行 323 ms** | 过滤 / 排序生成 SQL 表达式（`json_extract`），跨库对齐三条语义（大小写不敏感、数值 CAST、排序稳定性）；**物化索引列（B）留作后手** | M | 2 万行筛选 ≤ 50 ms；两库同一组用例行为一致 |
| **5** | **页面附件上传** | SDK 无上传方法；`connect-src 'none'`；素材只收图片 | `octopApp.upload(file)` → 父页面走 `POST /api/apps/{id}/upload`；owner 授权 + 大小上限 + 文件名白名单 | S | 页面能上传图片 / PDF 并在数据行里引用 |
| **6** | ~~**`data_version` 消费端**~~ **已完成（2026-11-15）** | `GET /api/apps/{id}/data-version`（轻量）+ 5 秒两段式轮询 + `data-changed` 事件；仅在页面可见时轮询（`useMiniAppDataVersion.ts`） | — | 已完成 | 两个浏览器 5 秒内看到对方改动，且轮询不触发全表读 |

#### P1 · 不阻断 MVP，但影响"像不像地基"

| # | 项 | 理由 |
|---|---|---|
| 7 | 能力位版本化（`CAPABILITIES_VERSION` 写进 manifest / `me()`） | 页面契约要能演进；现在新增方法时老应用无感知 |
| 8 | 权限语义收口（管理动作限 owner、`edit_schema` / `manage` 拆分） | 分支已做一部分；不做会让协作者越权 |
| 9 | 发布历史（现在一个应用一份快照，覆盖即丢） | 对外纠纷要能回溯"上周对外长什么样" |
| 10 | 数据归属指南（文档） | 不写的话用户会把权威数据放进 MiniApp，再发现导不出去 |

#### MVP 明确不做

| 不做 | 理由 |
|---|---|
| 事件收件箱 / 应用级 SSE | `infra/events` 的 `app.refresh` / `app.import` 已能把外部数据写进表；页面实时先用轮询兜住 |
| `light_app_schedules` | 数据源级 `schedule_seconds` 已覆盖"定时同步"这一唯一真实需求 |
| 公开看板分享 | 单应用公开链接已能对外；看板分享属分发层 |
| 同源视频播放 | 只在审片场景需要；先用关键帧图 + 外链绕开 |
| workflow runner | 违反 §1.2 定位，编排归 `infra/cron` / `infra/events` |
| Bundle v2 | 单实例备份 / 恢复已可用；跨主机迁移属分发层 |
| 多实例一致性 | 单实例 MVP 可接受；文档需写明「每实例限流、行数上限可能略超」 |
| **桌面 / 看板的功能补强** | 二者已形成完整闭环（看板服务端 CAS；桌面窗口/空间/任务控制/Dock）；剩余项见 `miniapp-overview.md` §2.9 / §2.12 的「完整度与缺口」。它们打磨的是展示层，而 §3.3 的 P0 在数据层 —— **先地基后橱窗**。唯一例外：桌面状态服务端化（现在只在 `localStorage`），它是"换设备即丢"的可用性问题，可单独提前 |

#### 里程碑切法

| 里程碑 | 内容 | 达成后 |
|---|---|---|
| **MVP-1「可信」** | ~~P0-1 行级变更历史 + P0-3 存储收口~~ **已完成（2026-11-17）** | **V2 审核场景成立**；数据可追溯、副本不再漂移 |
| **MVP-2「够快」** | P0-4 查询下推 A′ + P0-5 上传通道 | 万级台账不卡；审核能带附件 |
| **MVP-3「能关联」** | P0-2 reference 列 + aggregate 端点 | **V3 报表场景成立** |
| 之后 | P1 各项 + R2.1 外部服务 | 从「地基」到「平台」 |

**若只能做一件**：做 **P0-1 行级变更历史** —— 成本中等，但它是"能否用于业务"的分水岭；
没有它，MiniApp 永远停在"给 Agent 看结果的展示层"。

**若只能做两件**：加 **P0-3 存储收口**。它是结构性的：不做的话，每加一条写路径就多一份漂移，
后续所有功能都建在流沙上。

---

## 4. 路线图

原则：**先把已做的能力用起来，再开新战线；每阶段都有可独立发布的用户价值。**

> 本路线图服务于 §3.3 的地基 MVP 验收线。**阶段 1 / 1.5 与 P0 缺口部分重叠，
> 优先级以 §3.3 为准**；与验收线无关的阶段（阶段 3–6）可以滑档而不影响 MVP。

### 阶段 0：解锁（阻塞项，先于一切）

- **目标**：不新增用户功能，先让「合并、升级、权限」正确。
- **交付**：
  1. 上游合并 + 迁移重编号 + **幂等桥接迁移**（四条升级路径：全新 SQLite / 全新 PostgreSQL /
     旧 v31 库 / 上游 v24·v25 库）。
  2. `GroupAccess` / `AgentAccess` 适配器替换 `is_shared` 判断；访问申请从 app 推导 agent；
     owner 分裂处理（已完成，v35）。
  3. 权限语义收口：管理端点限 owner/admin；中期拆 `edit_schema` / `manage`。
  4. 访问申请 `pending` 唯一索引 + 两步批准（已完成，v35）。
- **验收**：迁移矩阵全绿；`tests/unit/db` 全绿；权限/能力/访问申请测试全绿；`make all` 绿。
- **明确不做**：任何新用户功能。

### 阶段 1：把已做的能力用起来（场景 A / B / D 的补齐）

> 进度（2026-11-15）：交付 1（页面写能力）、交付 2（行查询 filter/sort）、交付 3
>（`data_version` 实时刷新）与交付 6（快速模式合计行/表头行）已落地；交付 4–5 待做。

- **目标**：不改架构，补齐「差一口气」的读写能力，让填报、看板与审核真正可用。
- **交付**：
  1. **页面写能力补齐**：SDK 增加 `updateRow`（+ 批量写）——**已于 2026-11-15 完成**
     （`updateRow` / `updateRows` / `deleteRow`）；状态机跃迁校验发生在行更新时
     （`infra/apps/service.py:3236/3372`），行级 CAS（`expected_version` → 409）已生效。
  2. **行查询加 filter / sort**：`GET /api/apps/{id}/rows` 支持按列等值过滤与排序，
     服务端聚合复用 `charts.py` 的 `build_chart_spec`；解决几百条以上审核队列翻不动的问题。
  3. **`data_version` 实时刷新**：前端轮询 + `data-changed` 事件，两个浏览器不再互相看不见。
     **必须两段式**（先问版本号、变了才拉数据），否则等于每几秒全表扫描一次 —— 见 §5.6。
  4. **页面写操作接审计与通知**：页面发起的写操作与工具侧同一审计口径；状态变更后通知相关人。
     注意：`gateway.ws_hub.push_to_user` 是 **IM 网关**的通道（`infra/gateway/ws/ws_hub.py:142`），
     推不到 dashboard 浏览器 —— dashboard 侧要推送需另做（聊天 SSE 的模式，或应用级 SSE）。
     访问申请通知走 ws_hub 只对 IM 渠道有效（`api/routers/app_access.py:65-90`）。
  5. **页面上传通道**：`octopApp.upload(file)` → 父页面走已有
     `POST /api/apps/{id}/upload`（multipart）→ 返回工作区路径。需 owner 显式授权 +
     大小上限 + 文件名白名单（会把「任意文件写进 agent workspace」暴露给页面）。
  6. **Excel 快速模式补两处**：合计行在直接导入路径上也要剔除（现在只有检查路径剔除）、
     表头行显式传参。
- **验收**：填报场景可完成「新建 → 修改 → 推进状态」；审核场景可完成「筛选 → 打开一条 →
  通过/驳回（状态机生效）→ 写意见 → 导出记录」；看板可按状态筛选；两浏览器 5 秒内看到
  对方新增行；快速模式与直接导入的表头/合计行行为一致。
- **成本**：低（无新表、无新协议）。

### 阶段 1.5：同源媒体播放（场景 D 的另一半，可从阶段 1 拆出）

- **目标**：让页面能播平台托管的视频 —— 这是 MiniApp 从「数据视图」扩到「内容视图」的一步，
  不只服务审片台（课件预览、商品视频、培训录像回看、监控回看都受益）。
- **交付**：
  1. **CSP `media-src` 动态放开**：从静态 `data: blob:`（`dashboard/src/components/MiniAppFrame/appCsp.ts`）
     改为运行时注入 **具体 host origin + 媒体端点路径**。注意沙箱 iframe 是 opaque origin，
     `'self'` 匹配不到，必须写明 origin。
  2. **同源媒体端点**：`GET /api/apps/{id}/media/{name}`，**支持 Range 流式**（现有
     `/api/agents/{id}/media/preview` 是整文件进内存、无 Range，
     `api/routers/workspace.py:426-468`），走 app ACL 与公开可见性。
  3. **短时媒体令牌**：opaque origin 的 iframe 拿不到 `Authorization` 头 → 需要
     scope=media.read、绑定 app+user+资源、分钟级 TTL 的令牌，页面经 bridge 获取。
     与「平台级 capability token」方向一致（见 §6 取舍 6）。
  4. **写时扫描无需改动**：`_is_remote_url` 只认 `http://` / `https://` / `//`
     （`infra/apps/models.py:620-625`），相对 URL 天然放行。
- **验收**：审片台在页面内播放同源视频并支持拖动进度条；公开链接页只能读已发布快照内的媒体；
  未授权资源 403；令牌过期后不可用。
- **风险**：放宽 `media-src` 属于安全面扩大，须限定路径与来源，不做通配放开。

### 阶段 2：R2.1 外部服务（分水岭）

> 进度（2026-11-15）：schema v36、服务声明 CRUD、connector 只读调用、TTL 缓存/速率限制、
> 页面 SDK `service().call()` / `.image()`、Agent `app_add_service` / `app_list_services` /
> `app_remove_service`、图片资产、公开 asset 端点与公开缓存调用已落地；promote 待做。

- **目标**：让应用「活」起来——页面可经宿主调用外部只读数据源。
- **交付**：`light_app_services` / `light_app_service_cache`；服务声明（连接器实例归属、
  只读工具白名单、URL 前缀、SSRF 校验、响应 ≤5MB、超时 20s、每服务 30 次/分）；
  TTL 缓存 + 单服务配额；服务端拉图 → `assets/` → `octopApp.asset()`；公开面只读缓存端点；
  Agent 工具 `app_add_service` / `app_list_services` / `app_remove_service`；
  binding 从 `builtin.dataset` 切换到外部服务（promote 路径第一版）。
- **验收**：允许的只读工具可调用；未声明工具/URL 被拒；SSRF 被拒；缓存命中；
  公开页不能 live 调用；切换 binding 后 UI 与装配清单不变。
- **明确不做**：公开实时任意 URL（只允许结构化 adapter + `public_live`）。
- **理由**：这是场景 C 的前提，也是 MiniApp 从「静态看板」变成「活应用」的唯一分水岭。

### 阶段 3：R2.2 事件（瘦身版）

> 进度（2026-11-15）：schema v37、`light_app_events`、`app.inbox` 投递、游标读取/ack、
> 页面 SDK `events()` / `ackEvents()`、`on_create`/`on_update` 触发器、`app.import` 幂等 append 已落地；出站 `emitEvent()` + owner 声明的 Webhook 已落地（v42）；iframe 主动推送待做。
> 已知缺口：`light_app_events` 的幂等键尚未按 app 作用域、`ImportResult` 未写入 delivery、
> `app_event` WS 帧还没有 dashboard 消费者。见 [event-model.md](./event-model.md) §5。

- **目标**：页面能收到外部事件。
- **交付**：`light_app_events`（入站为主）+ `GET /events?since=` + ack；保留 500 条 / 30 天；
  推送到 dashboard 浏览器需要**应用级 SSE**（gateway 的 `ws_hub` 是 IM 通道，推不到浏览器），
  页面轮询只做可见性兜底（不每 App 独立 5–10 秒轮询）。
- **前提**：外部事件 → MiniApp **数据**这条链路已经通了（`infra/events` 支持
  `target_type=app`：`app.refresh` 重跑数据源、`app.import` 导入 payload 里的记录，
  `infra/events/worker.py:611-641`）。缺的是「页面能收到事件」，不是「事件能改数据」。
- **明确不做**：出站 webhook 出站面 —— 平台已有 `infra/events` 出站，不要造第二套事件总线；
  触发器（`on_create` / `on_update`）随事件一起做，不单独排期。
- **验收**：重复投递幂等；乱序不覆盖新数据；推送与轮询都可用；公开链接没有事件接口。

### 阶段 4：R2.3 应用级调度（只做两个动作）

- **目标**：把 `light_app_sources.schedule_seconds` 收敛成统一调度原语。
- **交付**：共享调度原语（DB 租约 + `next_run_at` + 失败退避，**不复用当前「前推一个间隔」的
  claim 语义**）；`light_app_schedules` 先支持 `sync_source`、`snapshot_publication`；
  旧 `schedule_seconds > 0` 自动迁移，旧列保留一个版本；时区用 `config.default_timezone`；
  详情页「自动化」页签。
- **明确不做**：`run_agent`（等配额与审批模型）、workflow runner（见 §6 取舍 3）。
- **验收**：多实例只执行一次；旧数据自动迁移；长任务不并发。

### 阶段 5：R2.4 公开看板（可滑档）

- **交付**：每格必须有有效发布快照；board token 可撤销/轮换；隐藏表、PII、撤销立即生效；
  `Cache-Control: no-store`；限流。公开实时外部调用仅限结构化 adapter + `public_live=1`。
- **滑档条件**：阶段 2 或 3 延期时，本阶段整体顺延到 R3。

### 阶段 6：R3 分发

- **6a Bundle v2（建议提前到阶段 2 之后）**：导出/导入 HTML、schema、行、assets、capabilities、
  装配清单；导入时重新 `validate_html`；缺失 capabilities 用**安全默认集**（不是 legacy 全开）；
  services/schedules 默认 disabled；connector 标记 `needs_binding`；**不含凭据/token**。
  API：`GET /api/apps/{id}/export`、`POST /api/apps/import`；
  CLI：`octop app export/import`。
- **6b IM 认证链接**：owner 一次性认证链接，**不要用会被链接预览消耗的 `GET /auth/app?code=`**，
  改成中转确认页 / POST，单次消费、短 TTL、`no-referrer`。群聊/非 owner 只发公开只读链接。
- **6c 渠道身份绑定（D4-lite）**：Dashboard 个人设置生成一次性绑定码，用户在 IM 私聊发送
  `/bind <code>`，写入 `channel_identities`。仅绑定到 agent owner 本人的发送者会签发个人链接；
  不切换 acting user，群聊不生效。同一 IM 身份不可覆盖绑定到其他用户。
  PR-11/12 已将其扩展为 `(channel_type, account_key, sender_id)`、`ToolScope` 工具收口与 `/link` 握手（schema v48）。
- **建议**：把「作用域能力令牌（capability token）」从 MiniApp 私有（game token / share token）
  提升为**平台级**身份原语，MiniApp、IM 认证链接、外部 origin 应用（游戏、外部工具）共用一套。

### 阶段依赖

```
阶段 0（解锁）
   └─► 阶段 1（补齐：写/查/协同/上传）──┬──► 阶段 1.5（同源媒体，可拆出并行）
                       │               ├──► 阶段 2（R2.1 外部服务）──► 阶段 6a（Bundle v2）
                       │               └──► 阶段 3（R2.2 事件）      ──► 阶段 4（R2.3 调度）
                       └──────────────────────────────────────────► 阶段 5（公开看板，可滑档）
阶段 6b / 6c 依赖阶段 0 的权限收口，可并行
```

---

## 5. 存储与派生副本

> 本章是设计讨论的落点：**权威源在哪里、哪些是刻意冻结的副本、哪些是没人生管的僵尸副本**。
> 三个待定决策在 §5.4，未拍板前不要按某一方案动代码。

### 5.1 现状：三个结构性问题

| 问题 | 现状 | 后果 |
|---|---|---|
| **① 物理模型：document-in-row** | 每行一个 `values_json` JSON blob，**没有值列索引**（`infra/db/migrations/028_light_app_rows.sql:13-40` 只建了 page / 幂等 / source 三个索引） | 按列的过滤与排序**无法走索引**：只能全表取回后在 Python 里过滤（`infra/apps/service.py` 的 `query_rows_detailed` → `infra/db/repos/apps.py:519-525` `list_all_data_rows`，**无 LIMIT**）→ 5 MB 的表一次请求全量解析 |
| **② 写入面：没有单一写入点** | 计数、manifest、`data_version` 的更新散落在 7 处 `set_table_stats`、3 处 `refresh_app_totals`，manifest 另有一处 `_refresh_manifest`（`infra/apps/service.py:1646-1672`） | 加一条写路径就可能漏一处 —— 漂移是结构决定的，不是偶然 |
| **③ 派生面：副本没有声明** | 工作区 CSV 空壳、manifest 第三份计数、助手历史里的图表快照、发布快照 —— 各自的「权威源 / 同步时机 / 过期行为」都没有写在任何地方 | 人和 Agent 无法判断哪个能信；已实测出现工作区 CSV 与 DB 行数分叉 |

### 5.2 权威源与派生副本清单

**权威源只有三个**：`light_app_rows`（行）、`light_app_tables.schema_json`（列定义）、工作区 `app.html`（界面）。其余都是派生副本。

| 实体 | 副本 | 性质 | 处置 |
|---|---|---|---|
| 行 | 工作区 CSV（主表 `data.csv`、附表 `tables/<key>.csv`） | v2 应用只剩创建时写的空壳，写入路径不回写（`_persist_rows` 仅在 v1 分支被调用） | **删**（§5.4 决策 2） |
| 行 | 导出 CSV / 备份包里的 `tables/<key>/data.csv` | 一次性生成，从 DB 渲染 | 保留（派生产物，非副本） |
| 行 | 助手历史里内嵌的图表序列（`infra/apps/assistant.py:55-83`） | 回答时刻的物化快照 | **降级**：UI 标注生成时间 |
| schema | 工作区 `schema.json` / `tables/<key>.schema.json` | v2 不回写 | 随 CSV 一并处理 |
| schema | 发布快照 `tables_snapshot_json` | 刻意冻结 | 保留 |
| 界面 | `app.html` / `previous_html` / 发布快照 `html_snapshot` | 运行版 / 回滚 / 对外冻结，三种职责 | 全部保留 |
| 计数 | `light_app_tables.row_count` · `data_bytes` | 列表页反规范化 | 保留，但要单一写入点 |
| 计数 | `light_apps.row_count` · `data_bytes`（各表求和） | 再聚合 | 保留；统一语义为「所有表之和」 |
| 计数 | `manifest.json` 里的 `row_count` | 第三份计数 | **删字段** |
| 变更信号 | `light_app_tables.data_version` | 写时递增，**没人消费** | 保留，补消费端（§5.6） |
| 能力 | `capabilities_json` | 权威 | 保留 |
| 可见性 | `public_visible`（实时）vs 发布快照（冻结） | 半快照语义已刻意设计 | 保留 |

### 5.3 方向：单一权威 + 显式派生 + 查询下推

```
写面（真相）  DB：light_app_rows + schema
               └─ 单一写入钩子 _after_write：计数 / manifest / data_version 一次算完
读面（查询）  过滤与排序下推到存储（当前完全没下推 → §5.4 决策 1）
派生面（副本） 只保留三类：发布快照（冻结）· 导出产物（一次性）· 计数缓存（反规范化）
文件面（工作区）app.html / assets / manifest —— 只留「给人与 Agent 看的目录」，不再放数据
```

**一条通用规则**（建议作为以后所有派生副本的准入条件）：
**每个派生副本必须显式声明「权威源 / 同步时机 / 漂移兜底」三件事；三者缺一，就是第四份真相。**
现在缺的正是「同步时机」和「漂移兜底」两栏。

### 5.4 三个待定决策

#### 决策 1：查询要不要下推到存储？

**已测量（2026-11-15，本机 SQLite，中位数 5 次）**。模拟真实台账（6 列、含备注长文本），
测的是 `GET /api/apps/{id}/rows?filter=&sort=` 的服务端路径：

| 行数 | values_json 体积 | **A. 现状（Python 过滤排序）** | **A′. SQL 下推（`json_extract`）** | **B. 物化列 + 索引** | 版本探测 |
|---|---|---|---|---|---|
| 200 | 35 KB | 6.3 ms | 0.4 ms（15.7×） | 0.1 ms（63×） | 0.01 ms |
| 1,000 | 174 KB | 17.7 ms | 1.8 ms（9.8×） | 0.3 ms（59×） | 0.01 ms |
| 5,000 | 869 KB | 78.4 ms | 7.3 ms（10.7×） | 0.3 ms（261×） | 0.01 ms |
| 20,000 | 3.5 MB | **323 ms** | **25 ms（12.8×）** | **0.3 ms（1077×）** | 0.01 ms |

读法：

- **现状是线性增长，约 16 ms/千行**。表的行数上限是 2 万 → **最坏一次筛选要 323 ms**
  （行更宽时接近 5 MB 上限会更慢）。
- **A′（把过滤排序写成 SQL 表达式）就有 10–16 倍**，而且**不改 schema、不重建表、不维护双写**。
  代价是要按库写表达式（SQLite `json_extract` / PG `values_json::jsonb->>`），
  并把「大小写不敏感比较、数值列按数值排序」这两条 Python 语义在 SQL 里对齐。
- **B（物化索引列）才与规模无关**（0.3 ms），但要动 schema 与写路径。
- **版本探测 0.01 ms** → 两段式轮询（§5.6）完全没有性能压力。

| 选项 | 做法 | 成本 | 结论 |
|---|---|---|---|
| **A. 保持 Python 过滤**（现状） | 全表取回后内存过滤 | 低 | **只在 2,000 行以内可接受**；与表上限 2 万行差 10 倍 |
| **A′. SQL 表达式下推**（新增选项） | 过滤/排序写成 SQL，值仍存在 JSON 里 | 低-中：一个 SQL 构造器 + 分库语法 | **建议先做**：10–16 倍，改动面小 |
| **B. 物化索引列** | schema 可声明 `indexed` 的列 → 抽成真实列 + 索引 | 中：schema 加概念 + 迁移 + 写路径维护 | 与 A′ 不冲突，可叠加；等真的需要万级实时筛排再做 |
| **C. 旁路查询投影** | 主表不动，另建异步物化的查询副本 | 高 | 读写彻底解耦，可上时序/列存 |

- **B 是跨库一致的"极限形态"**：SQLite 的表达式索引不能用于 JSON 函数（`json_extract` 非确定性），
  PostgreSQL 可以 —— 只有「物化成真实列」两边都能走索引。
- **B 的代价**：与「schema 演进零成本」有张力（加索引列需重建表）。建议只对用户主动声明为
  indexed 的列做，默认 0 个。
- **A′ 的风险点**：排序稳定性与空值处理要和 Python 版对齐；数值列要按 schema 类型生成 `CAST`；
  大小写不敏感比较要用 `LOWER(TRIM(...))`（这会让索引失效，但 json 函数本来也用不上索引）。
  跨库行为差异要用同一组用例对齐。
- **回退方案**：A′ 若在某个库上语义对不齐，就退回 A 并在 API 上明确
  「筛选/排序在应用侧执行，行数上限即性能上限」，同时把表的行数上限按实测下调。

#### 决策 2：v2 应用的工作区 CSV 删不删？

- **建议删**。它已不是数据源，留着必然分叉，且会误导 Agent（读到空表）与人。
- 代价：Agent 手册要改口径 —— **读数据用 `app_read_data`，不要读工作区 CSV**；v1 应用懒迁移成功后（`infra/apps/service.py:1493-1537`）删除其 CSV。
- 回退方案：若确有场景依赖工作区 CSV（例如人工用 Excel 二次加工），改为**导出时生成到 `exports/`** 而不是常驻工作区。

#### 决策 3：`AppDataStore` 抽象现在抽还是等 R2.1？

- **建议等**。`docs/miniapp-implementation-plan.md` 的 G5 存储门写得对：每应用独立 SQLite 只在「单实例 / 离线 / 强隔离」版本重新评估，且需要先有抽象与基准测试。
- R2.1 的 external service binding 就是**第二个实现** —— 等它落地再抽，抽象才有真实需求；现在抽是 YAGNI。
- 回退方案：若 R2.1 延期但「每应用独立存储」的需求真实存在（例如客户要求数据隔离），此时才抽接口，且第一版只支持 SQLite。

### 5.5 推荐落地顺序

| 步 | 内容 | 成本 | 收益 |
|---|---|---|---|
| **0** | ~~量一次 filter/sort 延迟~~ **（已完成，2026-11-15，见 §5.4 决策 1）** | — | 决策 1 有了依据：现状 16 ms/千行，20k 行 323 ms |
| **1** | 删 v2 工作区 CSV + manifest 去掉计数 | 低 | 消除会误导人和 Agent 的假真相 |
| **2** | **单一写入钩子 `_after_write`**（计数 / manifest / `data_version` 一处算） | 中 | 结构上根治漂移 |
| **3** | `data_version` 独立轻量端点 + 页面两段式轮询 | 低 | 页面实时性；版本探测实测 0.01 ms，无性能压力 |
| **4** | **A′：过滤/排序下推成 SQL 表达式** | 低-中 | 10–16 倍；不改 schema、不重建表 |
| **5** | B：物化索引列（等真的需要万级实时筛排再做） | 中 | 与规模无关（0.3 ms） |

### 5.6 联动：轮询必须两段式（已按此实现）

「`data_version` 实时刷新」如果直接轮询带 filter/sort 的行查询，就是**每几秒全表扫描一次**
（5 MB JSON × 多个打开的页面），会把控制面 DB 打满。因此必须是两段式：

```
GET /api/apps/{id}/data-version → { tables: [{ table_key, data_version, row_count }] }
                                   ← 极轻，只读计数列（实测 0.01 ms）
   ↓ 版本变了才拉
GET /api/apps/{id}/rows?...     → 真正的数据
```

**现状（2026-11-15）**：已按此实现 —— `GET /api/apps/{id}/data-version`（`api/routers/apps.py:1257`）
+ 宿主侧 5 秒轮询（`dashboard/src/components/MiniAppFrame/useMiniAppDataVersion.ts`，
仅在页面可见时轮询，版本变更后向 iframe 发 `data-changed`）。
页面侧契约：`octopApp.onDataChange` / `octop:data-changed` DOM 事件。

这也说明 **`data_version` 是"变更信号"的一等公民**：它有自己的轻量端点，不应塞在行响应里。

---

## 6. 关键取舍

| # | 取舍 | 结论与理由 |
|---|---|---|
| 1 | **先补已做能力，还是先开 R2.1** | **先阶段 1**。`updateRow` / filter / `data_version` 三件事成本低、直接决定填报、看板与审核场景是否成立；R2.1 之前应用是静态的，先让静态场景好用更重要 |
| 2 | **R2.2 事件做完整还是瘦身** | **瘦身**。只做入站 + 应用级 SSE 推送；出站交给 `infra/events`。不造第二套事件总线 |
| 3 | **要不要最小 workflow runner** | **不要**。平台已有 `infra/cron`、`infra/events`、`hitl_pending`、Teams；MiniApp 再长出 workflow 会把它推成低代码平台，违反 §1.2 的定位。编排属于编排层 |
| 4 | **R2.4 公开看板优先级** | **低，可滑档**。对外展示价值明确但依赖阶段 2/3 完成度；顺延到 R3 不影响核心场景 |
| 5 | **Bundle v2 提前** | **提前到阶段 2 之后**。它是「交付/迁移」的最小单元；R2.1 之后 services/sources 声明需要一起搬，越晚做迁移面越大 |
| 6 | **scope 与能力令牌归谁** | **平台级**。MiniApp 消费，不自造第三套令牌体系（现状已有 game token / share token 两套） |
| 7 | **多页/逐帧剪辑要不要做** | **不做**。逐帧时间轴、多轨、拖拽留在 React 侧；MiniApp 只做「看 + 判断 + 留痕」 |
| 8 | **审核/审片做 MiniApp 还是独立前端** | **做 MiniApp**（场景 D）。数据模型天然契合：状态机、行级范围、ACL、协作者、审计、导出、发布、嵌入全部现成；独立前端要重建表、状态机、权限与 Agent 集成。代价是必须先补 `updateRow`、上传与同源媒体三件 |
| 9 | **媒体能力开多大** | **只开同源 + 限定路径**（阶段 1.5）。不做通配放开、不做页面任意 URL；避免把沙箱改成一个不完整的浏览器 |
| 10 | **业务审批与 HITL 分不分** | **分开**。业务内容审核走 MiniApp 表规则状态机；`hitl_pending` 是「人批准 Agent 动作」，两者不是一回事，不要互相替代 |
| 11 | **查询要不要下推到存储** | **下推，但分两步**（§5.4 决策 1）。先做 A′（SQL 表达式下推，10–16 倍，不改 schema）；B（物化索引列）与 A′ 不冲突，等真的需要万级实时筛排再做。实测依据见 §5.4 |
| 12 | **v2 应用的工作区 CSV** | **建议删**（§5.4 决策 2）。它已不是数据源，留着必然分叉并误导 Agent；改为导出时生成到 `exports/` |
| 13 | **`AppDataStore` 抽象何时抽** | **等 R2.1**（§5.4 决策 3）。external binding 是第二个实现，届时抽象才有真实需求；现在抽是 YAGNI |
| 14 | **MVP 优先于路线图** | **是**（§3.3）。路线图是"平台往前走"，MVP 是"能不能当地基用"。冲突时先满足 §3.3 的 V1/V2/V3 验收线；阶段 3–6 可滑档 |
| 15 | **关系能力做到哪一层** | **只做引用校验 + 服务端聚合，不引入 join**（§3.3 P0-2）。完整关系层会把 MiniApp 推成低代码平台；主子表大数据量该用原生表 + React |
| 16 | **要不要为 MCP Apps 重写界面层** | **不重写，只做门面**（§1.4）。MCP Apps 换界面层、不换数据层；重画数据模型是最贵的开销且不产生地基能力 |
| 17 | **MCP 入口的优先级** | **在 §3.3 P0 之后**。宿主覆盖够用但缺 CLI 覆盖，且 `ui://` 资源需要匿名/token 访问（与取舍 6 的 capability token 同一件事） |

---

## 7. 度量

没有度量就无法判断这条路线对不对。每阶段结束时看这七个数：

| 指标 | 含义 | 期望方向 |
|---|---|---|
| 新建应用数 / 活跃创建者 | 是否有真实用户 | 上升 |
| 应用复用率 | 应用是否被反复打开，而不是一次性产物 | 上升 |
| Board 单元格填充率 | 应用是否真的进了工作台 | 上升 |
| 公开链接访问量 | 对外分发是否成立 | 上升 |
| 场景 A/B 闭环完成率 | 填报/看板是否走得完（新建→修改→推进→分享） | 上升 |
| 场景 D 闭环完成率 | 审核是否走得完（筛选→打开→通过/驳回→留痕→导出） | 上升 |
| 高风险能力开启率 | `external.fetch` / `events.emit` / `ai.generate` 的 owner 主动开启比例 | 反映真实集成深度 |

**反向信号**：若阶段 1 完成后场景 A/B/D 的闭环完成率仍低，说明问题不在能力而在**发现与引导**
（模板、创建入口、示例），此时应停止加能力，改为补模板与引导。

---

## 8. 风险与滑档

| 风险 | 影响 | 缓解 |
|---|---|---|
| 阶段 0 迁移桥接拖长 | 全线阻塞 | 先只做四条升级路径的启动验证，能力收口拆开 |
| 权限语义收口涉及 API 破坏性变更 | 前端与 Agent 工具同步改 | 与阶段 0 同批做，附 API 变更说明 |
| R2.1 外部服务的 SSRF / 凭据边界出错 | 安全事故 | URL 前缀白名单 + 复用 `infra/utils/ssrf_guard.py` 的 `validate_https_url(_resolved)` + 只读工具 + 响应上限 + 超时 + 每服务限流 + 公开面永不 live |
| 能力位继续膨胀 | 沙箱与权限模型失控 | 新能力一律显式开启、默认关闭；旧应用集合冻结（已落地） |
| 每进程限流在多实例下失效 | 配额被绕过 | 需要全局时用 DB 租约；文档注明每实例 |
| 文档漂移 | 三份文档互相矛盾 | 本文只写定位与顺序；现状以 overview 为准；执行细节以 implementation-plan 为准；历史决策留在 roadmap |

---

## 9. 相关文档

| 文档 | 职责 |
|---|---|
| [miniapp-overview.md](./miniapp-overview.md) | **实现现状的唯一入口**（功能矩阵、限额、缺口、风险） |
| [miniapp-product-plan.md](./miniapp-product-plan.md) | 本文：产品定位、取舍、阶段顺序、度量；§1.4 MCP Apps 分发面；§3.3 地基 MVP 验收线；§5 存储与派生副本 |
| [miniapp-sandbox-hardening.md](./miniapp-sandbox-hardening.md) | 沙箱结构、CSP/wrapper 依据；§6 与 MCP Apps 的关系及「门面优先」结论 |
| [miniapp-implementation-plan.md](./miniapp-implementation-plan.md) | 阶段执行细节、PR 顺序、决策门、验证清单 |
| [miniapp-platform-roadmap.md](./miniapp-platform-roadmap.md) | R2/R3 需求来源与历史决策（含被本文推翻的原始设想） |
| [miniapp-ai-native-plan.md](./miniapp-ai-native-plan.md) | AI 任务运行时、`usage_log` 归因、预算治理；任务/通知/用量复用的依据 |
| [video-workbench-implementation-plan.md](./video-workbench-implementation-plan.md) | 视频工作台落地方案（2026-11-23 修订）：外部扩展服务、V1/V2 分期、写服务/媒体/回写边界 |
| [ai-video-commerce-system.md](./ai-video-commerce-system.md) | 关联项目：审片台做 MiniApp、逐帧剪辑与经营 BI 留在 React 侧的分工依据；其 in-Octop 视频流水线落点已被上述方案取代 |
