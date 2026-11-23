# MiniApp 功能与架构现状

> 快照：2026-11-22，分支 `feature/miniapp-ai-native`，schema **v51**（049/050/051 已应用）；PR-11/12 已实现并部署（commit `f46f7a1c`）。
>
> 口径：**以当前代码与测试为准**。仓库里多份 `light-apps-*` / `miniapp-*` 文档是不同阶段的设计稿，只保留动机与历史记录，并在文首指向本文；本文把已落地的事实和明确未落地的部分分开写。
>
> 术语：统一使用 **MiniApp**；代码标识（`light_apps`、`light_app_rows`、`app_*` 工具）保持原样，不作为产品名。
>
> 实施顺序、数据所有权与收敛计划见 [MiniApp 实施落地计划](./miniapp-implementation-plan.md)。
>
> **产品定位、取舍与阶段顺序见 [MiniApp 产品定位与路线图](./miniapp-product-plan.md)。**
>
> **AI-native 增强开发计划见 [MiniApp AI-native 增强开发计划](./miniapp-ai-native-plan.md)。**
>
> **P1a/P1b/P2 已落地（2026-11-23）**：`light_app_tasks`（050）、Tier 1 `summarize_row`、Tier 2 `table_digest`（只读 `query_rows`/`aggregate` 工具循环）、`pre_review`（图文初审 + 固定列写回）与「内容审核台」系统模板、任务 API/SDK、`usage_log.app_id/task_id`（051）、每应用每日 token 上限、应用详情「AI 任务」页签、行操作「AI 任务」创建入口、4 个 Agent 任务工具（`app_run_task` / `app_get_task` / `app_list_tasks` / `app_cancel_task`）与 `GET /apps/{id}/task-types`、页面图片上传（`asset.write` + `octopApp.upload`）、行查询下推（SQLite `json_extract` / PG `jsonb->>`，2 万行约 47ms）、AI 任务完成/失败通知（`notifications` kind=`app_task`，点击进入应用任务页）。后续优先级见计划 §14.1：预算预扣/结算优先，任务 HITL 改为接入外部副作用时触发。
>
> **身份模型（v3）**：P0/P1 已落地并部署（PR-01~PR-10，schema v47，commit `aed36ded`）：`PrincipalDirectory` 与 platform/app/guest 三种 token scope、`authorize()` 授权内核、行归属与行级 PII、成员/邀请/加入码、guest 会话与门户、host policy、Dashboard 成员页与 `/join`。IM sender 身份（`account_key` + `sender_id`、`ToolScope`、app 数据工具收口）与 `/link` 握手（PR-11/12）已实现并部署。设计见 [MiniApp 用户管理与接入方案](./miniapp-identity-access-plan.md)，实施拆解见 [Identity 实施计划](./miniapp-identity-implementation-plan.md)。

---

## 0. 结论摘要

MiniApp 是 **Agent 交付物的着陆场**：Agent 用一组 `app_*` 工具创建一个带 schema 的数据表，再写一个自包含单文件 HTML 作为界面；数据行存在控制面 DB，HTML 存在 Agent 工作区，页面在无 JWT、无网络的沙箱 iframe 里运行，所有读写通过宿主桥代调。

当前实现已经不是“demo 级单页工具”，而是一套完整的小型应用平台：

| 维度 | 状态 |
|---|---|
| 数据底座 | ✅ 多表、行级 CAS、幂等、懒迁移、导入/导出、备份/恢复 v3、行级变更历史、服务端分组聚合、引用列 |
| 创建 | ✅ Agent 工具、Excel/CSV 快速模式（确定性渲染，不走 LLM）、系统模板 + 专家模板 |
| 界面 | ✅ 单文件 HTML、`window.octopApp` SDK、能力清单、双层沙箱、CSP、WebRTC 冻结 |
| 协作 | ✅ owner / admin / `edit_data` / `view`，协作者、应用交接、删除守卫 |
| 发布 | ✅ 只读公开链接、发布快照、公开表可见性、PII 脱敏、限流、撤销 |
| AI | ✅ 应用内只读数据助手（含图表）、一次性文本生成；均计入应用 owner 额度 |
| 数据源 | ✅ 工作区文件 + 只读连接器工具；手动/定时、游标增量、upsert、失败退避 |
| 游戏 | ✅ 白名单外部游戏、一次性会话 token、结果回写、侧栏运行 |
| 工作台 | ✅ 个人多 Board、12 列网格、缩放/锁定/撤销/全屏/自动排列 |
| IM | ✅ `app_share_link` 一次调用交付、纯文本格式化、单轮预算 |
| 集成/分发（R2/R3） | ◐ 外部服务、事件收件箱、出站 Webhook、应用级调度、IM 公开链接与 owner 身份绑定（D4-lite）已实现；公开看板、迁移包 v2 未实现 |
| 身份与访问（v3） | ✅ P0/P1（PR-01~PR-10）+ P2a（PR-11/12）已实现并部署，schema v48 |

关键数字：

- 42 个 `app_*` Agent 工具（含 3 个 setup plan 工具、3 个只读集成诊断工具、8 个 HITL 集成运维工具、4 个 AI 任务工具）；约 102 个 HTTP 路由 + 1 个公开宿主页（apps / public / boards / game；另有访问申请与认证链接路由）。
- 每个用户最多 50 个应用；每个应用最多 12 张表、每表 50 列、2 万行、5 MB。
- 单行读取上限 500；单文件 HTML 上限 512 KB；素材单文件 5 MB。
- 12 个应用能力位：11 个已接入执行链路，1 个为后续预留（`schedules.manage`）。
- 后端 `infra/apps/` 约 1.88 万行；应用 API 路由约 4,700 行；前端 MiniApp 页面/宿主约 1.8 万行（不含桌面 Shell；含桌面 Shell 约 3.1 万行）。
- 最近验证（2026-11-22）：PR-10 已部署（schema v47）；PR-11/12 定向验证——identity/gateway/agents 1879、apps 505、db 245、集成 103、前端 33 个用例，`make lint` / `make typecheck` 通过。线上服务已迁移到 schema v48。全量 `make all` 在本机跑到 88% 因资源被中止，尚未得到一次完整全绿记录。

选场景、落地路径与验收口径见 §5「应用场景与落地」。

---

## 1. 定位与边界

### 1.1 它是什么

| 维度 | 说明 |
|---|---|
| 数据 | 结构化表（schema + 行），存在控制面 DB，可被 Agent / 人 / 外部数据源写入 |
| 界面 | Agent 生成的单文件 HTML，运行在沙箱 iframe，通过 `window.octopApp` 读写自己的数据 |
| 交付 | 可发布成只读公开链接；可嵌入工作台看板、对话侧栏、IM 卡片 |
| 生命周期 | 归属一个 Agent 工作区；可交接给另一个 Agent；删除有守卫 |

### 1.2 它不是什么

- 不是微信/支付宝式原生小程序运行时：没有每应用进程、没有服务端执行页面代码、没有自定义域名。
- 不是业务数据库的权威源：定位是投影层；任务实体仍落原生表（见 `docs/team-autopilot-design.html`）。
- 不是实时协同编辑：没有 CRDT、行级/字段级权限、多人同时编辑 HTML。
- 不是审批系统：审批在 `hitl_pending`；MiniApp 只承载结果。
- 不是公开填报/公开写入系统：公开链接只读；外部写入必须走签名入站 Webhook，或由登录用户/协作者完成。
- 不是多租户隔离载体：单实例产品，公开链接是 bearer token 语义。

### 1.3 与相邻概念

| 概念 | 存什么 | 谁写 | 谁读 | 生命周期 |
|---|---|---|---|---|
| `threads` / `thread_messages` | 对话 | Agent + 用户 | 人 | 会话级 |
| `memory` | 记忆 | Agent | Agent | 长期 |
| `knowledge_bases` | 文档（RAG） | 用户 | Agent | 长期 |
| **MiniApp** | **结构化数据 + 界面** | **Agent + 人 + 外部同步** | **人（+ Agent 读回）** | **产品级** |

---

## 2. 功能地图

### 2.1 创建

| 入口 | 行为 | 状态 |
|---|---|---|
| Agent 工具 | `app_create` 定义字段并建主表；`app_write_html` 写界面；`app_append_row` 灌数据 | ✅ |
| 快速模式 | 工作台「创建」→ 上传 xlsx/xls/csv/tsv/json → 服务端 inspect → 用户确认字段 → 选视图 → 确定性渲染 HTML | ✅ |
| 本地模板 | 系统模板（包内 6 个）+ Agent 工作区 `app_templates/<id>/` | ✅ |
| 从模板创建 | `POST /api/apps/from-template`、`app_create_from_template`（`source` 为 `system` 或 `agent`） | ✅ |
| 云端市场 | 前端「云端市场」页签只有占位文案 | ⛔ |

**Setup Plan（从文件建应用）**：

- `app_inspect_file` 只读检查；`app_propose_plan` 生成 `app_setup_plans` 中的 `draft` 计划，**不创建应用**。
- 对话里的计划卡承载确认、预检和导入：确认后后端创建空应用并 dry-run；用户再点“执行导入”才写数据。所有步骤幂等（`plan_id + revision + source_hash + dry_run_hash`）。
- 计划支持多 sheet/多表：`tables` 列表一次确认、一次导入，每张表独立 dry-run 结果；服务/能力可随计划一起声明，计划卡内可编辑，确认时按 `save_service` / `set_capabilities` 的同等约束预检（连接器归属、工具白名单、URL 前缀、请求头、TTL/限流、Owner-only 能力），导入后按同一确认执行。
- 计划进行中 `app_create` / `app_import_rows` 会被工具拒绝（`app_cancel_plan` 可退出）；`dry_run=true` 不再触发 HITL，正式导入仍走 HITL（非计划路径）。
- 破坏性/安全操作（删除、改结构、删行、公开/分享、集成写操作）不能并入计划，仍逐项强制审批。
- 旧 `app_inspect_card` 只读；计划卡从 `GET /api/apps/setup-plans/{plan_id}` 回显状态，历史刷新不会重复出现“确认并继续”。
- 审计：`audit_log` 记录 `miniapp.setup_plan.propose / confirm / execute / cancel / expire / supersede`；详情接口返回按时间排序的 audit 事件。管理员可用 `GET /api/apps/setup-plans` 按状态/agent/用户/app/线程/是否有错误分页查询，详情用现有 `GET /api/apps/setup-plans/{plan_id}`。

快速模式细节：

- 文件上限 50 MB；服务端 `inspect_file()` 给出 sheet、表头行、列类型、非空率、枚举候选、样本、PII 标记。
- 支持单 sheet 和多 sheet：多 sheet 时每张 sheet 建一张表，第一张映射到 `main`。
- 视图三选一：`dashboard`（KPI + 趋势 + 分类 + 明细）、`ledger`（筛选 + 分页表格）、`board`（按状态/阶段分组）。
- **不调用 LLM**：`viewgen.py` 纯函数渲染，秒级完成、结果稳定、可单测。
- 失败回滚：创建或导入失败会删除半成品应用。
- 创建后仍可让专家继续用 `app_write_html` 重写界面。

### 2.2 数据

- **多表**：每个应用从 `main` 开始，最多 12 张表；每表独立 schema、行；备份/导出时生成 CSV 快照。
- **行存储**：`light_app_rows`，`values_json` 存 `{column: string}`；行级 `version` 做 CAS。
- **行级变更历史**：`light_app_row_history`（v40）记录 `update` / `delete` / `replace` 的 before/after、actor、source；`GET /api/apps/{id}/rows/{row_id}/history` 读取，viewer 看到 PII 脱敏值；应用调度器按 90 天清理。自动数据源同步（replace/upsert）不写行历史，避免每次整表覆盖放大成 O(rows)/次；手动导入、Agent 与页面写入仍记录。
- **服务端聚合**：`GET /api/apps/{id}/aggregate?table=&group_by=&metric=&agg=&filter=&limit=` 按列分组并聚合 `sum/avg/count/min/max`；PII 列不能分组、度量或过滤；页面用 `octopApp.aggregate(options)` 调用，不再拉全表到前端计算。
- **行查询下推**：`GET /rows?filter=&sort=` 在 SQLite 用 `json_extract`、PG 用 `values_json::jsonb->>` 过滤/排序/计数，只取当前页；2 万行实测约 **47ms**（目标 ≤50ms）。聚合仍是服务端 Python 分组，未下推。
- **引用列**：列类型 `reference`，schema 带 `ref_table` / 可选 `ref_label`；值存目标表的 `row_id`，写入/导入时校验目标行存在（`APP_ROW_INVALID`），空值允许。`GET /rows?expand_refs=true` 返回 `refs[column][row_id] = label`；label 列默认取目标表第一个非 PII 字符串列，读取时按目标表 `pii` / `row_scope` 脱敏。被引用行删除返回 `APP_ROW_IN_USE`；`replace` 导入涉及仍被引用的表时同样拒绝。不做级联、多对多或跨应用引用。公开链接不自动展开 `refs`：页面可对公开目标表自行 `listRows()` 映射，私有表/敏感 label 不会因此暴露。
- **实时刷新（登录态宿主）**：`light_app_tables.data_version` 每次行写入递增；宿主每 5 秒轮询 `GET /apps/{id}/data-version`，变化时向 iframe 发送 `data-changed`，页面用 `octopApp.onDataChange(cb)` 或 `octop:data-changed` DOM 事件局部刷新；页面不可见时暂停轮询。公开链接暂不轮询。
- **行级范围**：`light_app_tables.row_scope` 为 `app`（协作者共享）或 `user`（普通用户只看自己的行，owner/admin 看全部）；`user` 表不会出现在公开链接。`row_scope=user` 按 `COALESCE(row_owner_user_id, created_by)` 过滤；`participate` 只能写自己的行。
- **身份与访问（v3，PR-01~10）**：`users.kind` 区分 member / guest；`light_app_tables.member_visible` 约束 `participate` 可读表，隐藏表返回 404；`light_app_rows.row_owner_user_id` 与 `created_by` 分离，读取 / 写入统一走 `COALESCE(row_owner_user_id, created_by)`；`authorize()` 统一按角色 × 表可见性 × 行归属 × PII 判定。guest 只能持 `participate`，`row_scope=user` 只看自己的行，自己的 PII 不脱敏、他人行脱敏。
- **幂等**：`client_request_id` 唯一索引；外部源 `(source_id, source_key)` 唯一索引。
- **懒迁移**：`storage_version=1` 的旧应用首次读/写时把 CSV 灌入 DB，成功后置 2；失败保留 v1 可重试。
- **Schema 演进**：`app_alter_schema` / `PATCH /schema` 支持改名、删除、改类型；整表先校验，失败整次拒绝。
- **导入**：CSV/TSV/JSON/xlsx/xls；`sheet`、`header_row`、`dry_run`、`upsert_key`、多文件 `paths`、列映射、失败行回传；合计/小计行在检查与直接导入两条路径都会跳过，快速创建未显式传 `header_row` 时使用检查报告检测到的表头行。
- **导出**：`GET /api/apps/{id}/export` 从 DB 渲染 CSV 快照。
- **备份/恢复**：zip（manifest + app.html + preview.svg + tables/*/schema.json + tables/*/data.csv + tables/*/row_ids.json + assets/*）；`GET /backup`、`POST /restore/preflight` 与 `POST /restore`，格式 v3，解压上限 256 MB。v2 用 `row_ids.json` 记录原始行 id；v3 的 manifest 还带表可见性/`row_scope`/规则和 `integrations` 声明（入站入口、出站 Webhook、外部服务、应用数据源、`sync_source` 调度），但不含任何 secret。恢复时生成新 id 并按表重映射引用与 `source_id`；集成声明重建为 disabled，Owner 重新轮换/绑定密钥后启用。`snapshot_publication` 调度依赖公开链接，不在归档内，恢复时跳过。v1/v2 归档仍可恢复。
- **恢复预检**：`POST /restore/preflight` 在写入任何行之前检查归档结构、表 schema、CSV 行与 `row_ids` 配对、行内引用完整性、HTML/预览、入口映射目标、connector 实例、数据源/调度引用和 Webhook URL；错误会阻断恢复，缺失 connector 或会被禁用的声明以 warning + 处理建议返回，Dashboard 展示后再确认导入。`POST /restore` 内部复用同一预检，错误时不会创建应用。
- **PII**：列级 `pii` 标记 + 值级检测；导入默认跳过敏感列（必填敏感列拒绝并要求显式 `include_pii=true`）；viewer / 公开链接 / 助手 / 导出统一脱敏。

限额：

| 项 | 上限 |
|---|---|
| 应用 / 用户 | 50 |
| 表 / 应用 | 12 |
| 列 / 表 | 50 |
| 行 / 表 | 20,000 |
| 单表数据 | 5 MB |
| 单元格 | 10,000 字符 |
| HTML | 512 KB |
| 素材 | 5 MB / 文件 |
| 预览 SVG | 256 KB |
| 协作者 / 应用 | 50 |

### 2.3 界面与宿主

- **单文件 HTML**：CSS/JS 全内联；禁止远程脚本、样式、图片、字体、媒体、`fetch()`、`WebSocket`、`EventSource`、`sendBeacon`、外链 `<a>`、`meta refresh`、`location` 导航、`window.open`、`RTCPeerConnection`、嵌套 frame/object/embed。
- **SDK**：`me()`、`schema(table)`、`tables()`、`listRows(options)`（支持 `filters` / `sort` / `expandRefs`）、`aggregate(options)`（服务端 group_by/agg）、`appendRow(row, options)`、`updateRow(rowId, values, options)`、`updateRows(rows, options)`、`deleteRow(rowId, options)`、`generate(prompt, options)`、`launchGame(gameId)`、`composeMessage(text)`、`openExternal(url)`、`asset(name)`、`onDataChange(cb)`、`events(options)` / `ackEvents(ids)`、`emitEvent(type, payload, options)`（需 `events.emit` + owner 声明的 Webhook）、`service(name).call(tool, args)` / `.callDetailed(tool, args)`（返回 `value` / `cached` / `expires_at`） / `.image(url)`（需 `external.fetch`）。
- **写行**：`updateRow` / `updateRows` / `deleteRow` 走 `PATCH|DELETE /api/apps/{id}/rows/{row_id}`，支持 `expectedVersion` 做 CAS；状态机规则在更新时校验；`row_scope=user` 的表只能改自己创建的行。`updateRows` 逐行提交，返回 `{updated, failed, failures}`，单行失败不影响其它行。
- **能力清单**：`light_apps.capabilities_json`；旧应用空值 = R1 全开（后续新增能力不自动继承）；新应用默认安全集；显式 `[]` = 页面不能做任何操作。
- **宿主页面**：
  - 应用详情页：iframe + 「界面 / 数据」双 Tab + 助手抽屉 + 分享/发布/能力/数据源/备份入口。
  - 对话：`app_card` 卡片、侧栏 MiniApp 标签、对话内 MiniApp 列表（从 transcript 的 `app_card` 推导）。
  - 工作台：看板单元格直接渲染 MiniApp。
  - 公开链接：`/p/{token}` 服务端渲染宿主页，只读 SDK。

### 2.4 协作与权限

| 角色 | 权限 | 说明 |
|---|---|---|
| owner | `edit_structure` | 创建者；始终全权 |
| admin | `edit_structure` | 系统管理员绕过 |
| collaborator | `view` / `edit_data` | 按应用 ACL；最多 50 人 |
| collaborator（`edit_structure`） | schema / 数据 / HTML 编辑 | 不能删除、交接、分享、发布、改能力或表可见性 |
| 公开链接访问者 | 只读 | token 即凭证；不能写、不能 generate、不能 launchGame |

要点：

- **Agent 可访问 ≠ 应用可访问**：创建应用要求可访问该 Agent（owner/shared/admin）；读写按应用 ACL。
- **工具侧与 HTTP 侧同源**：`_get_for_actor` 与 `permission_for` 使用同一套判定。
- **应用交接**：`POST /api/apps/{id}/transfer` 复制已知文件并更新 `agent_id`；源文件 best-effort 保留。
- **删除守卫**：删除仍有应用的 Agent 返回 `AGENT_HAS_APPS`（409），需 `force=true`。
- **`app_delete` 强制 HITL**：`MANDATORY_HITL_TOOLS` 包含 `app_delete`，需要用户批准。
- **团队 host 不挂 `app_*` 工具**：团队 host 使用 board tools / 交付物来引用 MiniApp（`artifact_type="app"`）。
- **外部参与者（P1 已开放）**：guest 与 `participate` 已接入授权与行级 PII；app owner 可创建邀请 / 加入码，host policy 可要求宿主审批；guest 通过 `/join` 进入、在 `/my` 查看已加入应用。`PUT /shares` 仍是旧的全量平台协作者接口，保留 guest 行但不经过 host policy（已知旁路）。IM sender 身份与 `/link` 已在 PR-11/12 实现：数据工具按 sender / 群聊收口，绑定私聊可拿个人入口码。

### 2.5 发布与公开链接

- **默认关闭**：只有显式创建/分享才产生公开链接。
- **token**：32 字节随机值，DB 存 SHA-256；明文在 `secrets` 表保留一份，用于 `app_share_link` 复用同一链接；`rotate=true` 才轮换。
- **发布快照**：发布时冻结当前 HTML + 公开表结构（表清单、列定义、标签、`public_visible`）；数据行仍实时读。
- **公开表**：主表默认公开，附加表默认私有；隐藏表立即从公开面消失，即使已发布。
- **行级公开**：`published` row scope 已取消，`public_visible` 是唯一的公开表开关；`row_scope=user` 的表永不公开。只公开部分行时，建独立公开表/投影（内部表保持 `public_visible=false`），由审核动作、事件或调度同步；页面侧过滤不构成访问控制。
- **PII**：公开读取 `redact_pii=True`；快照列按 **快照 PII ∪ 实时 PII** 脱敏。新增 PII 标记立即生效，解除 PII 标记需重新发布。
- **路由**：`GET /api/public/apps/{token}`、`GET /api/public/apps/{token}/rows`、`GET /api/public/apps/{token}/assets/{name}`、`POST /api/public/apps/{token}/services/{name}/call`（只读缓存）、`GET /p/{token}`；JWT 豁免，`Cache-Control: no-store`，按 token+IP 限流。
- **撤销/过期**：`revoked_at` 或 `expires_at` 命中后 404。
- **审计**：创建/撤销/公开访问写 `audit_log`。
- **不支持**：公开写入、公开事件、公开看板分享。

#### 部分行公开：独立公开表/投影

`public_visible` 是表级开关，公开 rows API 返回整张公开表，不能表达“只公开 `status=approved` 的行”。需要行子集公开时：

1. 内部表保持 `public_visible=false`（如 `orders`）。
2. 新建公开表（如 `orders_public`），只保留安全列，设置 `row_scope=app`、`public_visible=true`。
3. 审核通过时把选中行写入公开表（`app_append_row` / `app_update_row` / 页面写能力）；`app_append_row` 可带 `client_request_id` 保证幂等，导入/同步路径可用 `source_id`/`source_key`；撤回时删除或标记。
4. 发布/重新发布公开链接，公开页只读取公开表。

```text
orders            public_visible=false   全量内部数据
  └─ 审核通过 / 事件 / 调度同步 ─► orders_public
                                   public_visible=true
                                   仅安全列 + 可公开行
```

- **自动化**：R2.2 触发 `on_update`（例如 `status → approved`）或 R2.3 `sync_source` 定时同步；在事件/调度落地前用 Agent 工具或页面写操作手动同步。
- **安全**：公开表只放安全列；PII 标记仍然生效（公开读取统一脱敏）。页面侧过滤不是访问控制，公开 rows API 会返回整张公开表。
- **不做**：不要为行子集新增 `published` row scope 或 `internal_visible`；需要复杂谓词时再评估表级 `public_filter_json`。

### 2.6 AI 助手与文本生成

**应用内只读助手**（`POST /api/apps/{id}/assistant`，SSE）：

- 只读工具：`app_tables`、`app_query_rows`（可按列等值过滤 + 单列排序，`sort=-col` 倒序）、`app_chart`。
- 上限：8 个模型轮次、回放 12 条历史、问题 4,000 字、回答 8,000 字。
- 图表：bar/line/pie/area/scatter/stacked_bar；后端用真实行数据聚合，禁止模型编造序列。
- 过程展示：SSE 额外发送 `thinking` / `tool_start` / `tool_end`；思考文本、工具名/参数/结果（截断）与引用行随回答一起持久化，`/assistant/history` 原样返回，刷新后助手抽屉仍显示「深度思考 + 工具调用」折叠过程。过程元数据以内容尾部 marker 存储，无需数据库迁移；回放给模型时只保留可见问答。
- 历史按 `(app, user)` 分区；回答引用 `row_id`；敏感列脱敏。
- 计费与限额归属**应用 owner**，不是提问者。
- 每应用 LLM 并发 2、每分钟 30 次；超限 `APP_BUSY`（429）。

**一次性生成**（`POST /api/apps/{id}/generate`）：

- 页面通过 `octopApp.generate(prompt, {system})` 调用；无历史、无工具、不读应用数据。
- 需要 `ai.generate` 能力位 + `view` ACL；服务端强制校验能力位。
- 使用 owning agent 的默认模型；计入 owner 额度；90 秒超时、输出上限 16k 字符。
- 目前**不支持按应用选择模型**。

### 2.7 数据源与导入

| 类型 | `kind` | 行为 |
|---|---|---|
| 工作区文件 | `workspace_file` | 按路径重新导入；支持 `upsert_key`、`include_pii` |
| 外部连接器 | `external` | 调一个只读 MCP/连接器工具，取结果 JSON，按 `result_path` 提取记录 |
| 手动运行 | — | `POST /api/apps/{id}/sources/{source_id}/run` |
| 定时运行 | — | 保存 `schedule_seconds > 0` 自动生成 `light_app_schedules`（`sync_source`）；30 秒扫描 + DB 租约/心跳领取；失败指数退避，上限 24h |

约束：

- 一张表只能绑定一个数据源。
- 外部工具必须是只读（`_require_read_only`）；参数与结果路径可配置；支持 `cursor_arg` / `cursor_path` 增量游标，成功后写回 `params_json`。
- `mode=append|replace`；`replace` 在导入结果为空时拒绝，避免把表清空。
- 数据源同步按 `(source_id, table, mapping, 批次内容)` 生成 `client_request_id`：超时/崩溃后重试同一批次会跳过已写入行；内容或映射变化视为新批次。追加型“全量快照”源仍应配置 `upsert_key`，否则每次新批次都会追加整表。
- 外部数据源与 `light_app_services` 是两条路径：数据源同步到表；服务调用按次读取并缓存。

#### 外部服务调用（R2.1）

- **声明**：`light_app_services`（schema v36）记录页面可调用的服务名、provider、connector 实例、只读工具白名单、URL 前缀、缓存 TTL、每分钟调用上限；owner/admin 通过 `/apps/{id}/services` 管理，保存时同步一个同名 connector/external binding。
- **公开 HTTP 一等公民**：保存服务时 `connector_instance_id` 可留空，服务端复用或自动创建当前 owner 的 `http-extract` 实例；工具为 `fetch_json` / `fetch_text` / `fetch_html`，必须配置 URL 前缀。服务声明还可设置受限请求头（`User-Agent`、`Referer`、`Accept`、`Accept-Language`），用于有反爬默认头要求的公开页面。`public_live` 仍只读缓存、不会发起 live 调用。这样简单公开 JSON/文本接口不需要先手工创建连接器。
- **页面调用**：`octopApp.service("douban").call("fetch_json", {url})` → bridge 检查 `external.fetch` → `POST /apps/{id}/services/{name}/call`。需要展示“是否缓存/何时过期”时用 `.callDetailed(...)`，返回 `{value, cached, expires_at}`。
- **约束**：只支持 in-process gateway connector 的只读工具；参数中的 `url` 必须命中声明的前缀；适配器自带 SSRF/5 MB/20 s 限制；每服务默认 30 次/分钟；结果按 TTL 缓存到 `light_app_service_cache`，缓存命中不占实时配额。
- **图片**：`service(name).image(url)` → 服务端按同一前缀/SSRF/5 MB 规则拉取 → 存 `assets/` → 页面用 `octopApp.asset(name)`；公开链接新增 `GET /api/public/apps/{token}/assets/{name}`。
- **公开链接**：`public_live=true` 的服务可经公开页 `service(name).call()` 读取 **缓存**；端点只查 `light_app_service_cache`，不会发起 live 调用。
- **豆瓣 adapter（2026-11-18）**：`douban` gateway connector 提供 `search_books` / `get_book` / `get_cover`；读书记录工作台声明 `douban` 服务即可查询，封面走 `serviceImage`，服务未填 URL 前缀时自动带 `img*.doubanio.com` 白名单。
- **模板服务声明**：模板目录下的 `services.json` 会由 `create_from_template` 自动声明；`connector_kind` 为空凭据 gateway connector 时按 owner 复用/自动创建实例（`reading-tracker` 已内置 `douban`）。页面仍需 owner 在应用详情页开启 `external.fetch`。
- **尚未做**：builtin→external 的 promote 切换。

#### 事件收件箱（R2.2 第一版）

- 外部事件订阅支持 `app.inbox`：worker 把事件写入 `light_app_events`，目标上按外部 event id 幂等；保留最新 500 条 / 30 天。
- 页面通过 `GET /api/apps/{id}/events?since=&limit=` 按游标读取，`POST /api/apps/{id}/events/ack` 标记已处理；SDK 对应 `octopApp.events()` / `ackEvents(ids)`。
- 每次投递同时向 app owner 推送 `app_event` 帧；**dashboard 目前还没有消费这个帧**，页面必须轮询/启动时拉取，WS 只是预留的加速通道。
- 表规则可配置 `triggers.on_create` / `on_update`（可带 `when.field`/`when.equals`），行写入成功后写一条收件箱事件；默认事件类型 `row.created` / `row.updated`。
- `app.import` 默认改为 `append`，按外部 event id 生成 `client_request_id`，重试幂等；需要整表覆盖时在 mapping 里显式传 `mode="replace"`。
- 公开链接没有事件接口。
- **已修复（2026-11-17，v41）**：
  - `light_app_events` 幂等键改为 `UNIQUE(app_id, event_id)`；同一外部事件 fan-out 到多个 app 时各自读取自己的 inbox。
  - `app.import` / `app.refresh` 的 `ImportResult` 写入 `external_deliveries.result_json`（imported/updated/skipped/error_count/warning_count/failed_count/pii_columns/blocked_pii/partial，不落行值）；`GET /api/external-events/deliveries` 返回 `result`。
- **仍缺口**：
  - iframe 内 `onEvent` 回调、CloudEvents 仍未做。
  - PostgreSQL 并发 ingest / team seq 的冲突安全、`expert.turn` 重试去重仍未做。
- 术语与投递保证见 [event-model.md](./event-model.md)。

#### App-owned 入站入口（v43）

- **归属**：`light_app_ingress` 记录 app 自有的入口；每个入口自动创建一个 `external_sources` 行（`kind=app_ingress`，config 带 `app_id` / `ingress_id`）和一条 `app` 订阅。source owner 跟随 app owner，app 删除时级联清理，不再依赖全局 External Events 页面。
- **模式**：`app.inbox`（写收件箱）、`app.import`（写入表；mapping `{table, columns}`）、`app.refresh`（触发已有数据源；mapping `{source_id}`）。事件类型可选精确过滤；`max_per_minute` 0–600。
- **API**：`GET/POST /api/apps/{id}/ingress`、`PATCH/DELETE .../{ingress_id}`、`POST .../{ingress_id}/rotate-secret`、`POST .../{ingress_id}/test`。创建返回 `hook_url` + 一次性 secret；rotate 同样只返回一次。
- **发送**：`POST /api/hooks/{source_id}`，`X-Octop-Timestamp` / `X-Octop-Signature`；`verify_webhook` 同时接受 `webhook` 和 `app_ingress` 两种 source kind。payload 上限、去重、投递重试与普通 webhook 一致。
- **UI**：应用详情抽屉「入站入口」区块支持创建/编辑/删除、轮换密钥、发送测试事件；mapping 以 JSON 编辑，保存前校验表和列是否存在。
- **Agent**：`app_integration_create_ingress` / `update_ingress` / `delete_ingress` / `test_ingress` 走强制 HITL，用户确认后执行；创建结果包含 `hook_url` 和一次性 secret。

#### 出站 Webhook（R2.2 出站，v42）

- **声明**：`light_app_webhooks` 记录 owner 声明的目标 URL、事件类型（精确名、`order.*` 前缀、`*`）、启用状态和签名密钥；页面不能指定目标地址。每个 Webhook 自动挂一个内部 `external_sources` 行（`kind=app_outbound`，config 带 `app_id`）和一条 `webhook.post` 订阅，复用现有 outbox/worker，不新增投递引擎。
- **密钥**：创建时生成 `secrets.token_urlsafe(32)`，用 `secret_repo` 加密存 `secret_blob`，只在创建/轮换响应里返回一次；`PATCH .../webhooks/{id}` 传 `rotate_secret=true` 轮换。UI 在应用详情抽屉的「出站 Webhook」区块管理。
- **页面调用**：`octopApp.emitEvent(type, payload, {summary, clientEventId})` → bridge 要求 `events.emit` → `POST /api/apps/{id}/events/emit`；服务端再次 `require_capability`，按事件类型匹配启用中的 Webhook，逐条 `ExternalEventService.ingest`。`client_event_id` 作为 `external_id`，同一 Webhook 的来源内重试去重。
- **请求**：`POST` JSON envelope `{event_id, event_type, occurred_at, received_at, source_id, app_id, summary, payload}`；头 `X-Octop-Event-Id`、`X-Octop-Timestamp`、`X-Octop-Signature: sha256=HMAC_SHA256(secret, timestamp + "." + raw_body)`。
- **约束**：URL 只允许 http(s)、不能带账号密码，创建和每次投递都过 SSRF 校验；不跟随重定向，10 秒超时，响应读取上限 64 KB；2xx 成功，408/425/429/5xx 重试，其它 4xx 直接 dead-letter；payload ≤ 64 KB；每应用 4 并发 / 60 次每分钟；投递至少一次，接收方按 `event_id` 或业务键幂等。
- **公开链接**：公开只读桥不支持 `emitEvent`，避免匿名访问者触发 owner 的外部副作用。
- **管理边界**：内部 `app_outbound` source 在 Settings → External Events 只读展示（可查事件、投递并重试），不能删除、停用或改订阅；所有变更走应用详情的「出站 Webhook」或 integration API。应用删除时内部 source/subscription 一并清理。
- **不得**：payload 选择目标、页面持有密钥、为出站另建队列或 EventBus，见 [event-model.md](./event-model.md) §6。

#### Agent 集成运维

owner/admin 可以让 Agent 使用两组工具：

**只读诊断**

- `app_integration_list`：列出该应用的 app-owned 入站入口、source/订阅、出站 Webhook、调度和外部服务；
- `app_integration_health`：检查 source 错误/停滞、订阅停用、mapping 与当前 schema 漂移、目标 app 写权限、dead/failed 投递、失败调度，返回带 `code` / `suggested_action` 的问题列表；
- `app_integration_trace`：按内部 `event_id` 或来源 `external_id` 追踪 source → delivery → 收件箱/写入行。

**受控写操作（强制 HITL）**

- `app_integration_create_ingress` / `update_ingress` / `delete_ingress` / `test_ingress`：创建/修改/删除 app-owned 入站入口，或发送测试事件；
- `app_integration_create_webhook` / `update_webhook` / `delete_webhook` / `test_webhook`：创建/修改/删除出站 Webhook，或发送出站测试事件。出站 Webhook 的 secret 不返回 Agent，由 Owner 在应用详情复制。

创建入口的结果包含 `hook_url` 和一次性 secret；Agent 应只展示一次，后续消息不重复。团队 host 不挂这组工具。

**风险分级 / 最小权限 / 审计（2026-11-18）**

- `infra/apps/tool_policy.py` 给每个 `app_*` 工具标注 `read` / `write` / `sensitive` 和 `content` / `data` / `integration` / `security` / `lifecycle` 分组。
- HITL 集合与风险分级分开维护（`APP_HITL_TOOL_NAMES`）：风险分级仍决定 Agent 可按 `app_tool_max_risk` 挂哪些工具；HITL 只覆盖不可逆、会丢数据或对外发送的操作，即使全局 HITL 关闭也不能静默执行。
- 搭建类工具（`app_import_rows` / `app_add_service` / `app_remove_service`）在用户于审批卡显式选择「放通这些工具（本对话）」后，可对本次对话的同类调用跳过重复审批；`app_alter_schema`、删行、删除应用、创建/修改入站与出站集成仍属于 `MANDATORY_HITL_TOOLS`。
- 条件审批：`app_share_link` 仅在 `rotate=true`（会使旧链接失效）时确认；`app_set_table_visibility` 仅在 `public=true` 时确认；`app_integration_update_ingress/webhook` 仅在 `enabled` 不为 false 时确认；`app_integration_test_ingress` 仅在 `app.import` / `app.refresh` 模式确认；集成删除与 `app_integration_test_webhook` 不再确认。
- 审批前预检：`app_add_service` 使用 `provider=connector` 却没有 `connector_instance_id`，或 `app_import_rows` 使用 `dry_run=true`，会跳过审批卡直接返回结果，避免用户为必然失败或只读的调用点批准。
- Agent 配置可用 `app_tool_max_risk`（`read` / `write` / `sensitive`，默认 `sensitive`）和 `app_tool_groups`（列表，省略=全部）做最小权限过滤；非法 risk 回退为 `read`，显式空列表表示不挂 app 工具。
- 每个 `app_*` 工具调用都会写 `miniapp.agent_tool` 审计：actor、agent_id、user_id、app_id、risk、group、hitl_required、参数键、参数 SHA-256、状态、耗时、异常类型；不记录原始参数、PII 或 secret 值。

### 2.8 外部游戏

- 游戏 URL 只能在 `config.json` 的 `games` 白名单里；MiniApp 只能传 `game_id`。
- `POST /api/apps/{id}/game-sessions`：校验 `edit_data` + `game.launch` 能力位，签发 2 小时一次性 JWT。
- 游戏在侧栏独立 iframe 运行，token 放在 URL fragment；不持有 dashboard JWT。
- 结果通过 `POST /api/game-sessions/{token}/results` 回写；`append` 按 `session_id + op + row` 幂等，`upsert` 按 `upsert_key`。
- 单次最多 10 个 operation、每 operation 1,000 行；写入时重新校验 ACL。
- 公开链接拒绝 `launchGame`。

### 2.9 工作台看板（Board）

- 每个用户多个 Board；独立 URL `/apps/boards/{board_id}` 与全屏 `/apps/boards/{board_id}/view`。
- 12 列网格；每个单元格 `{app_id,x,y,w,h,locked}`；最多 24 个应用。
- 拖拽/缩放、锁定、撤销、自动排列；展示页可全屏并隐藏顶部工具栏，按 ESC 退出。
- 布局保存带 CAS 版本，冲突提示并重载。
- 管理员可在自己的 Board 放置任意应用；普通用户只能放自己可访问的应用。
- 被应用 ACL 阻断的用户可在权限卡片发起访问申请（`app_access_requests`）；应用所有者与专家所有者都会收到通知，各自完成自己那一半（应用所有者授予 `view` / `edit_data` / `edit_structure` 协作者权限，专家所有者共享专家）。只有申请人同时能访问专家和应用后才会标记 `approved`，否则保持 `pending` 等待另一方；批准与 ACL 写入在单事务内完成，重复/并发批准只有一个赢家。
- 首次加载把旧 `localStorage` 单 Board 无损迁移为默认 Board。
- **仅个人看板**：没有 `share_token_hash`，没有公开看板接口。

完整度与缺口（2026-11-15 核对）：

| 维度 | 现状 | 判定 |
|---|---|---|
| 服务端持久化 | `miniapp_boards.layout_json` + CAS 版本，冲突提示并重载 | ✅ |
| 布局操作 | 12 列网格、拖拽、缩放、锁定、撤销（历史 20 步）、自动排列 | ✅ |
| 多板 | 板列表 + 图库（预览图、创建/改名/删除）+ 切换器 | ✅ |
| 全屏展示 | `/apps/boards/{board_id}/view`，独立路由、懒加载、ESC 退出 | ✅ |
| 单元格内容 | 仅渲染 MiniApp（复用 `MiniAppFrame`，继承能力位与桥） | ⚠️ 不能放标题/文本/分隔线/指标卡 |
| 单元格上限 | 24（`MAX_BOARD_ITEMS`） | ⚠️ 「每屏一应用」的墙会撞顶 |
| 自动刷新 | 无板级 `refresh_seconds`（原设计有），依赖每个 MiniApp 自身的 `data_version` 轮询（5 秒） | ◐ 可接受 |
| 公开分享 | 无 `share_token_hash` / `/public/boards/*` | ⛔ 有意不做（roadmap P3），单应用公开链接可替代 |
| 模板 / 预设布局 | 无 | ⛔ 低价值，等真实多板用户 |
| 板内搜索 / 筛选 | 无 | ⛔ 应用多时才成问题 |

### 2.10 IM 通道

- `app_share_link`：按 app_id 或名称查找 → 服务端复用/创建公开链接 → 返回纯文本 + 可选封面；默认不轮换 token。
- `app_open`：dashboard 来源返回 `app_card`；IM 来源返回公开链接/纯文本，避免把 `octop_ui` 卡片发给不支持的通道。
- 微信/QQ：`format_channel_text()` 去掉 Markdown；`withhold_weixin_stream_preamble()` 丢弃最终答案前的中间旁白。
- 单轮预算：`StreamBudget(max_tool_calls=20, max_seconds=180, max_repeats=3)`，超限取消本轮并提示。
- 公开链接需要 `OCTOP_PUBLIC_BASE_URL`（或 `config.json.public_base_url`）才是绝对地址；否则返回相对路径。
- **渠道身份绑定（PR-11）**：Dashboard 个人设置生成一次性绑定码，用户在 IM 私聊发送 `/bind <code>`；绑定到 agent owner 本人的发送者标记 `owner_verified`，其他 member / guest 绑定记录在 `sender_user_id` 供入口码分流。不切换 acting user，群聊不生效；同一 `(channel_type, account_key, sender_id)` 不可覆盖绑定到其他用户。
- **D3 认证链接**：`app_access_codes` 存 SHA-256 code；`app_share_link(mode="auto")` 在已绑定的私聊成员、`public_base_url` 已配置时返回 `/login/app-access?code=...`，10 分钟单次有效；SPA 确认页点击后才调用 `POST /api/auth/app-access/exchange` 换取 **app-scoped JWT**（2 小时，只允许该 app 路由和只读启动接口），并直接进入独立路由树中的全屏运行时（不挂载 Dashboard 外壳）。认证链接只登录用户自己，app 权限仍由现有 ACL 校验。
- **当前边界**：D4-lite 最初只绑定 owner；PR-11 起支持任意 member / guest 的私聊绑定。群聊、未绑定用户仍走公开只读链接。P1 已提供 guest 会话与 Web `/join`；PR-11/12 已加入 `account_key` / `sender_id`、`ToolScope` 与 app 数据工具收口，IM 私聊里的绑定成员 / guest 可拿个人入口码，非 owner / 群聊不能调用数据工具。

### 2.11 安全与运维

- **写入时校验**：`validate_html()` + `find_external_refs()` 在 `app_write_html` 阶段拒绝外链、frame、网络 API、导航 API、WebRTC。
- **渲染时双层沙箱**：固定 wrapper 文档（`frame-src 'none'`）包住 App iframe（`sandbox="allow-scripts"`）；wrapper 转发消息，第二次 `load` 视为逃逸并熔断。
- **WebRTC / frame 冻结**：App 脚本前注入不可配置的 `undefined`，封堵 `createElement`、`innerHTML`、`DOMParser`、`Range` 等 frame 创建路径；MutationObserver 兜底；SDK 每次请求前做不变量自检。
- **违规上报**：CSP 违规 / 沙箱完整性失败 → `/api/apps/{id}/security-events` → 限流 → metrics + audit；公开页暂不上报。
- **服务端能力校验**：`generate`、`game-sessions` 在服务端调用 `require_capability`，不只依赖前端桥。
- **指标**：`light_app_open_total`、`light_app_row_write_total`、`light_app_export_total`、`light_app_assistant_total`、`light_app_generate_total`、`light_app_public_view_total`、`light_app_csp_violation_total`、`light_app_sandbox_integrity_total`、`light_app_llm_rejected_total`、`light_app_public_rate_limited_total`。
- **限流**：每进程；多 worker 是每 worker 一份预算，不是全局。
- **已知缺口（2026-11-17）**：应用目录写保护 + 读取/发布复验已落地；破坏性/公开操作的 prepared operation、page-facing 高风险端点的**完整覆盖**仍暂缓——当前已覆盖 `generate` / `game-sessions` / `services/{name}/call` / `events/emit`。详见 [MiniApp 使用场景、开发方式、技能与安全边界](./miniapp-use-cases-and-security.md) §0.1，不要按「已覆盖」处理。

### 2.12 桌面模式（AgentOS Shell）

- 工作台顶部「看板 / 桌面」切换；进入桌面自动全屏并播放「智管家」启动动效。
- 每个可访问的已启用专家都有独立桌面（即使还没有小程序）；只展示自建或他人共享的专家；管理员与其他用户一致，不提供跨账号浏览未共享专家的模式。「全部应用」是跨专家的全局小程序库，从它打开应用会跳到所属专家桌面。
- 新的访问申请通过 `/api/notifications/ws` 的 `app_access_request` 帧实时推送给应用所有者与专家所有者；桌面通知中心与全局 Toast 提示，Toast 可直接批准/拒绝。部分批准后会再通知另一方完成剩余授权；最终批准/拒绝通过 `app_access_resolved` 帧通知申请人。客户端每 5 分钟做一次兜底轮询（仅页面可见/聚焦时），不依赖高频请求。
- 窗口统一由 `desktopWindows.ts` 的 Window Registry 描述（`app` / `chat` / `external` 的 id、最小尺寸、标题、图标与持久化规则）；窗口位置、大小、层级和最小化/最大化状态恢复到 `localStorage`。
- 窗口拖动用合成层 `transform` 预览、缩放时保持内容起始尺寸做视觉缩放，松手一次性提交，避免 iframe 每帧重排和边缘白线。
- Dock 高度按实际渲染测量后避让，窗口最大化与放置都不会压住 Dock；Dock 最多展示 10 个小程序，其余从「全部应用」进入。
- 窗口内容层按圆角裁剪，MiniApp wrapper 与 iframe 背景透明，深色桌面下不再露出白边。
- Dock 2.0 汇总当前桌面的小程序、专家会话和外部网页窗口，显示运行/最小化状态，右键提供显示、最小化、关闭；末尾是「全部应用」启动器。
- Mission Control（菜单栏按钮或 `F3`）一次展示所有专家桌面及打开的窗口，点击窗口会切回所属桌面并聚焦。
- 智能体动态中心汇总运行中的专家、全部打开窗口和未处理的审批/安全提醒；桌宠会按专家状态显示空闲、工作、告警或离线。
- 通知中心支持小程序审批、专家回复、任务、安全与定时任务等类型；当前由桌面会话产生审批/回复通知，其余类型供后续事件源接入。
- 已打开窗口仍只通过 sandboxed MiniAppFrame、桌面会话或外链 webview 渲染，桌面 Shell 不新增任何后端权限。

完整度与缺口（2026-11-15 核对）：

| 维度 | 现状 | 判定 |
|---|---|---|
| 窗口模型 | `app` / `chat` / `external` 三类；稳定 id、bounds、z 序、最小化/最大化/还原、每窗口绑定空间 | ✅ |
| 窗口交互 | 拖拽移动、拖拽缩放、边界钳制（含 Dock 避让）、红绿灯控件、合成层预览 | ✅ |
| 多空间 | 按专家自动生成（Expert desktops，含「全部应用」全局库） | ✅ |
| 全局交互 | 命令面板 `Cmd/Ctrl+K`、Mission Control `F3`、空间切换 `Cmd/Ctrl+←/→`、`ESC` 退出 | ✅ |
| 系统部件 | Dock 2.0、通知中心、智能体动态、全局 Toast、桌宠、启动动效 | ✅ |
| 外链窗口 | 任意 URL 开窗，sandbox `allow-scripts allow-forms allow-modals`，带地址栏与「在浏览器打开」兜底 | ✅ |
| 隐藏窗口资源 | 最小化/跨空间立即暂停 5 秒 `data-version` 轮询；隐藏超过 30 分钟卸载 iframe 并显示休眠占位，恢复时重载；专家会话窗口不卸载 | ✅ |
| 通知持久化 | 访问申请、HITL 审批与结果写入服务端 `notifications`（schema v39），通知中心启动/聚焦/打开时同步；聊天回复提示仍为客户端临时 | ✅ |
| **状态持久化** | **仅 `localStorage`（`octop:apps-desktop-windows:v1`，上限 30 窗口）** | ⚠️ **结构性问题**：与看板的服务端 CAS 模型不一致；换设备/换浏览器即丢 |
| 空间自建 | 空间由专家推导，用户不能自定义分组或重命名 | ⚠️ 影响「个人工作区」 |
| 窗口吸附 / 平铺 | 无半屏、四分之一屏、自动铺排 | ⚠️ 体验主要落差 |
| 多选 / 分组 | 无 | ⛔ 收益低 |
| 键盘覆盖 | 仅空间切换、命令面板、任务控制；无关闭/最小化/切窗快捷键 | ⛔ 低-中 |
| 通知偏好 | 通知中心有，但无「哪些应用可弹」的开关 | ⛔ 低 |

---

## 3. 架构

### 3.1 全景

```
                    Agent (LLM)                 Dashboard                IM channel
                         │                          │                        │
                  app_* tools                /api/apps, /api/boards     app_share_link
                         │                          │                        │
                         ▼                          ▼                        ▼
              ┌──────────────────────────────────────────────────────────────────┐
              │                    AppService (infra/apps/service.py)            │
              │  lifecycle / ACL / tables / rows / import / publication /        │
              │  sources / services / app events / schedules / backup / transfer │
              └───────┬──────────────────────┬──────────────────────┬────────────┘
                      │                      │                      │
                      ▼                      ▼                      ▼
             LightAppRepo            BackendWorkspace        Assistant / Generator
             (SQLite/PG)             app.html / assets       owner's agent model
                      │                 preview.svg / mirrors   + quota + usage
                      ▼
        light_apps / light_app_tables / light_app_rows
        light_app_collaborators / light_app_assistant_messages
        light_app_sources / light_app_schedules / light_app_services
        light_app_publications / light_app_events / light_app_webhooks
        light_app_ingress / app_setup_plans / app_access_codes
        miniapp_boards / notifications

  Browser:
  Dashboard host ── postMessage bridge ── wrapper iframe (fixed doc)
                                              └─ App iframe (opaque origin, no JWT)
  Public:
  /p/{token} host page ── same wrapper ── read-only bridge ── /api/public/apps/*
```

### 3.2 模块职责

| 文件 | 职责 |
|---|---|
| `infra/apps/service.py` | 应用生命周期、ACL、表/行、schema 演进、导入导出、发布、数据源、备份恢复、交接 |
| `infra/apps/models.py` | Schema/Column/行校验、CSV 序列化、HTML 写入校验、外链/导航扫描 |
| `infra/apps/capabilities.py` | 能力目录、解析、校验、新应用默认集 |
| `infra/apps/sandbox.py` | 服务端 CSP、wrapper 文档、WebRTC 冻结、静态 frame 检查、违规 URL 脱敏 |
| `infra/apps/tools.py` | 42 个 `app_*` LangChain 工具、看板/分享链接载荷 |
| `infra/apps/tool_policy.py` | 工具风险分级（read/write/sensitive）、分组与 HITL 集合 |
| `infra/apps/hitl_preflight.py` | 条件审批预检（哪些调用可以跳过审批卡） |
| `infra/apps/integration_ops.py` | 集成诊断与运维工具实现 |
| `infra/apps/webhooks.py` | 出站 Webhook 声明、签名与 event outbox 复用 |
| `infra/apps/ingress.py` | app-owned 入站入口、source/subscription 生命周期 |
| `infra/apps/dataset.py` | `builtin.dataset` 只读契约、导出/导入 |
| `infra/apps/limits.py` | 应用 LLM 与公开面限流 |
| `infra/apps/wiring.py` | AppService / ingress / webhook 工厂 |
| `infra/apps/assistant.py` | 只读数据助手（SSE、工具循环、图表、历史） |
| `infra/apps/generation.py` | 一次性文本生成 |
| `infra/apps/charts.py` | 图表请求校验与后端聚合 |
| `infra/apps/aggregate.py` | 服务端分组聚合（PII 列不可分组/度量） |
| `infra/apps/model_access.py` | 解析应用所属 Agent 的默认模型 |
| `infra/apps/pii.py` | 表头/值级 PII 检测与脱敏 |
| `infra/apps/inspect.py` | Excel/CSV/JSON 结构分析 |
| `infra/apps/importer.py` | 文件解析为行 |
| `infra/apps/viewgen.py` | 快速模式确定性 HTML 渲染 |
| `infra/apps/templates.py` | 系统模板注册表 + Agent 模板信息 |
| `infra/apps/preview.py` | 封面 SVG 校验 |
| `infra/apps/sources.py` | 连接器只读工具调用 |
| `infra/apps/scheduler.py` | 应用级调度循环（DB 租约 + 心跳；`sync_source` / `snapshot_publication`） |
| `infra/apps/boards.py` | 个人看板布局与 CAS |
| `infra/apps/game_sessions.py` | 游戏 token 签发/校验 |
| `infra/apps/session.py` | 从对话 transcript 提取 app_card id |
| `infra/apps/prompt.py` | 向 Agent 系统提示注入 MiniApp 规则 |
| `infra/db/repos/apps.py` | 应用/表/行/协作者/助手历史/数据源/发布 SQL |
| `infra/db/repos/boards.py` | 看板 SQL |
| `api/routers/apps.py` | 应用 HTTP 面（约 86 个路由） |
| `api/routers/public_apps.py` | 公开只读 API + `/p/{token}` 宿主页 |
| `api/routers/boards.py` | 看板 CRUD / 布局 |
| `api/routers/game_sessions.py` | 游戏结果写入 |
| `api/routers/app_access.py` | 应用访问申请 |
| `api/routers/app_access_link.py` | IM 一次性认证链接预览/兑换 |
| `api/routers/channel_bindings.py` | IM 身份绑定码/绑定列表 |

### 3.3 数据模型

| 表 | 用途 | 关键字段 |
|---|---|---|
| `light_apps` | 应用元数据 | `app_id`、`agent_id`、`owner_user_id`、`storage_version`、`capabilities_json`、`previous_html*`、计数 |
| `light_app_tables` | 表注册 | `table_key`、`schema_json`、`schema_version`、`data_version`、`public_visible`、`row_scope`、`member_visible`、行数/字节 |
| `light_app_rows` | 行数据 | `row_id`、`values_json`、`version`、`source`、`source_id/source_key`、`client_request_id`、`row_owner_user_id` |
| `light_app_collaborators` | 协作者 ACL | `user_id`、`permission` |
| `light_app_assistant_messages` | 助手历史 | `app_id`、`user_id`、`role`、`content` |
| `light_app_sources` | 数据源绑定 | `table_key`、`kind`、`mode`、`schedule_seconds`、`params_json`、`mapping_json`、运行状态 |
| `light_app_schedules` | 应用级调度 | `schedule_id`、`app_id`、`action`、`target_json`、`interval_seconds`、`next_run_at`、`lease_until`、`last_status`、`failure_count` |
| `light_app_publications` | 公开链接 | `token_hash`、`expires_at`、`revoked_at`、`html_snapshot`、`tables_snapshot_json`、`snapshot_digest` |
| `miniapp_boards` | 个人看板 | `board_id`、`owner_user_id`、`layout_json`、`version` |
| `app_access_requests` | 访问申请 | `request_id`、`app_id`、`agent_id`、`requester_user_id`、`app_owner_user_id`、`agent_owner_user_id`、`needed`、`status`；同一 requester/target 仅一条 `pending` |
| `light_app_bindings` | 服务绑定 | `binding_id`、`app_id`、`provider`、`service_id`、`config_json`、`enabled`、`version` |
| `light_app_services` | 外部服务声明 | `service_id`、`app_id`、`name`、`provider`、`connector_instance_id`、工具白名单、URL 前缀、TTL、每分钟上限 |
| `light_app_service_cache` | 服务结果缓存 | `service_id`、`tool`、`args_hash`、`response_json`、`expires_at` |
| `light_app_events` | 事件收件箱 | `event_id`、`app_id`、`event_type`、`source`、`summary`、`payload_json`、`acknowledged_at` |
| `light_app_webhooks` | 出站 Webhook | `webhook_id`、`app_id`、`name`、`url`、`event_types_json`、`source_id`、`subscription_id`、`secret_blob`、`enabled` |
| `light_app_ingress` | app-owned 入站入口 | `ingress_id`、`app_id`、`name`、`mode`、`source_id`、`subscription_id`、`enabled` |
| `app_setup_plans` | 从文件建应用的计划 | `plan_id`、`thread_id`、`agent_id`、`user_id`、`source_path`、`status`、`revision`、`app_id`、`dry_run_json`、`result_json` |
| `app_access_codes` | IM 一次性认证链接 | `code_hash`、`app_id`、`user_id`、`permission`、`channel_type`、`account_key`、`sender_id`、`expires_at`、`consumed_at` |
| `channel_identities` | IM 身份绑定 | `channel_type`、`account_key`、`sender_id`、`user_id`、`verified_at`；唯一 `(channel_type, account_key, sender_id)`；旧行 `account_key=''` 继续兜底 |
| `channel_link_codes` | WebView → IM 的 `/link` 握手码 | `code_hash`、`user_id`、`app_id`、`expires_at`、`consumed_at` |
| `channel_bind_codes` | IM 绑定码 | `code_hash`、`user_id`、`expires_at`、`consumed_at` |
| `notifications` | 用户通知收件箱 | `notification_id`、`user_id`、`kind`、`title`、`body`、`path`、`read_at`、`dedupe_key` |
| `light_app_row_history` | 行级变更历史 | `app_id`、`table_key`、`row_id`、`version`、`action`、`before_json`、`after_json`、`actor`、`source`、`changed_at` |
| `light_app_tables.row_scope` | 行级范围 | `app`（协作者共享）或 `user`（普通用户只看自己创建的行） |
| `light_app_tables.member_visible` | 成员可见性 | 已约束 `participate` 可读表；默认 0，owner 在成员页逐表开启 |
| `light_app_tables.rules_json` | 表规则 | v1 状态机：`state_field` + `transitions`；可选 `triggers.on_create/on_update`（写收件箱） |
| `light_app_rows.row_owner_user_id` | 行归属 | 已参与行过滤与行级 PII；读取统一 `COALESCE(row_owner_user_id, created_by)` |
| `users.kind` | 主体类型 | `member` / `guest` 已参与登录、Principal 解析与运行时授权 |
| `goal_deliverables` | 团队交付物 | `artifact_type` 支持 `app`，`artifact_ref=app_id` |

迁移编号：025（应用/协作者/多表/助手/数据源）→ 026（交付物）→ 027（外部事件）→ 028（行入库）→ 029（公开链接）→ 030（表可见性/能力清单/HTML 回滚/看板）→ 031（访问申请）→ 032（服务绑定）→ 033（行级范围）→ 034（表规则）→ 035（访问申请双 owner / pending 唯一索引）→ 036（外部服务声明/缓存）→ 037（应用事件收件箱）→ 038（应用级调度）→ 039（通知收件箱）→ 040（行级变更历史）→ 041（事件幂等作用域 / delivery 结果）→ 042（出站 Webhook）→ 043（app-owned 入站入口）→ 044（服务请求头）→ 045（setup plans / channel identity / app access codes）→ 046（MiniApp identity primitives）→ 047（collaborator status / invites / join codes / app_sessions / host policy）→ 048（IM identity v2 / access-code sender fields / channel link codes）。当前 `_schema_version = 48`，`tests/unit/db/test_db_pool.py` 断言为 48。

### 3.4 存储布局

每个应用在 owning Agent 工作区下：

```
apps/<app_id>/
  app.html            # 唯一必写产物，Agent 生成的完整页面
  preview.svg         # Agent 画的封面（app_set_preview）
  manifest.json       # 只读镜像：app 元数据 + 表清单（不含行数）
  schema.json         # 主表 schema 镜像（历史兼容）
  tables/<key>/
    schema.json
  assets/<name>       # PNG/JPEG/WebP/GIF，≤5MB
```

行数据在 DB；工作区文件是 `app.html`、`assets/`、`preview.svg` 和 schema/manifest 镜像。v2 应用不再写空的 `data.csv`；只有 `storage_version=1` 的旧应用还保留 CSV，首次读写时懒迁移到 DB，迁移后的旧 CSV 不再列出（保留为回滚副本）。远程后端（COS/S3/OSS/OBS/docker）通过 `BackendWorkspace` 读写，Octop 不直接拼路径。

### 3.5 关键链路

**A. Agent 创建应用**

```
LLM → app_inspect_file（可选）
    → app_create（写 light_apps + light_app_tables + 工作区 schema/manifest）
    → app_write_html（validate_html → workspace app.html）
    → app_append_row / app_import_rows（校验 → light_app_rows + 计数）
    → app_set_preview（SVG 校验 → preview.svg）
    → app_open（返回 app_card；对话里出现入口）
```

**B. Dashboard 打开应用**

```
GET /api/apps/{id} + /html
  → buildSrcDoc(HTML) + buildWrapperDoc()
  → host iframe (sandbox=allow-scripts)
      → wrapper iframe (CSP frame-src 'none')
          → app iframe (srcdoc, opaque origin)
  → app 的 octopApp.* → postMessage → bridge.attachBridge
      → 能力位检查 → appsApi.* → /api/apps/...（JWT）
```

**C. 公开链接**

```
app_share_link / POST publication
  → publish(): 冻结 HTML + 公开表结构，token hash 入库
  → /p/{token} 服务端渲染宿主页
      → wrapper → app iframe
      → 只读桥 → /api/public/apps/{token}[/rows]
      → live rows + 实时 PII/表可见性
```

**D. 应用级调度**

```
light_app_schedules(enabled=1, next_run_at <= now)
  → AppSourceScheduler.tick() 每 30s
  → claim(): lease_until 条件 UPDATE（多实例只执行一次）
  → 心跳每 60s 续租；每个动作有硬超时（同步 15 分钟、快照 2 分钟）
  → execute_schedule():
       sync_source          → run_source()（保存数据源时自动镜像）
       snapshot_publication → 刷新已存在公开链接的 HTML/表结构快照
  → finish(): 记录 last_status / last_error / next_run_at / failure_count
  → 首次运行、成功和失败都加确定性 jitter，避免集中打爆连接器
  → 成功/失败从完成时间重排，避免长任务完成后立即重跑
  → 失败指数退避（以 interval 为基数，上限 24h）
```

`GET/POST /api/apps/{id}/schedules`、`PATCH/DELETE /api/apps/{id}/schedules/{schedule_id}` 管理应用级任务；公开链接弹窗可开启定时刷新快照。调度按 interval 运行，不受时区影响；未来 cron 型动作再使用 `config.default_timezone`。

**E. 应用内助手 / 生成**

```
POST /api/apps/{id}/assistant
  → acquire_app_llm()（并发/分钟限流）
  → AppAssistant.prepare()（解析 owner agent 模型、检查额度）
  → SSE: token / tool / chart / done / error
  → app_tables / app_query_rows / app_chart（只读，PII 脱敏）

POST /api/apps/{id}/generate
  → require_capability(ai.generate)
  → app_llm_slot()
  → AppGenerator.generate()（owner 模型、owner 额度）
```

**F. 游戏**

```
octopApp.launchGame(game_id)
  → POST /api/apps/{id}/game-sessions
  → 校验 edit_data + game.launch + 白名单
  → 签发 2h JWT（app/user/game/session）
  → 侧栏 GameFrame 打开 <url>#token=...
  → POST /api/game-sessions/{token}/results
  → append/upsert 写入 light_app_rows
  → postMessage session-finished → MiniApp 重新加载
```

### 3.6 权限模型

MiniApp 有两套正交的权限：

| 维度 | 控制对象 | 规则 |
|---|---|---|
| **用户 ACL** | 登录用户对应用的读写 | owner/admin = `edit_structure`；协作者 = `view` / `edit_data` / `edit_structure`（后者不含管理动作） |
| **页面能力清单** | iframe 页面能向宿主请求什么 | `capabilities_json`；旧应用按 R1 全开（新增能力不自动继承）；新应用安全集；`[]` 全关 |

执行点：

- 页面方法：`bridge.ts` 的 `checkMethodAllowed`；未知方法默认拒绝。
- 昂贵/外部副作用端点：服务端 `require_capability`（`generate`、`game-sessions`、`services/{name}/call`、`events/emit`）。
- Agent 工具：`app_create` / `app_set_capabilities` 只能设置低风险能力；`ai.generate`、`game.launch`、`external.fetch`、`events.emit`、`schedules.manage` 由 Owner 在应用详情页开启。
- 数据端点（`/rows`、`/assets`）：以用户 ACL 为准，能力位只约束页面代码，不约束用户本人。
- 公开面：token 即凭证；只读；不接受 JWT。
- 管理员在 HTTP 路由与 `app_*` Agent 工具中均按 `edit_structure` 放行；在他人 Agent/应用上的写操作写入 `miniapp.admin_override` 审计，桌面会话顶部显示「管理员代理操作」横幅。
- 权限拒绝以 `octop_ui.renderer = app_permission_card` 的结构化结果返回：对话中显示当前身份、专家/小程序归属与所需权限，桌面模式同时写入「需要权限」通知。
- 协作者权限支持 `view` / `edit_data` / `edit_structure`；`edit_structure` 只覆盖 schema、数据和 HTML 编辑。删除、交接、分享、公开链接、能力设置、表可见性仅 owner/admin。
- 被阻断用户可在权限卡片发起访问申请（`app_access_requests`）；应用所有者与专家所有者分别完成自己那一半（授予应用权限 / 共享专家），两步都完成后才标记 `approved`。

能力清单现状：

| 能力 | 页面方法 | 默认新应用 | 执行状态 |
|---|---|---|---|
| `data.read` | `schema` / `tables` / `rows` / `aggregate` | 开 | ✅ |
| `data.write` | `append` / `update` / `updateMany` / `remove` | 开 | ✅ |
| `asset.read` | `asset` | 开 | ✅ |
| `asset.write` | `upload` | 关 | ✅ 页面上传（PNG/JPEG/WebP/GIF，≤5MB） |
| `external.open` | `openExternal` | 开 | ✅ |
| `chat.compose` | `composeMessage` | 开 | ✅ |
| `events.read` | `events` / `ackEvents`（R2.2） | 开 | ✅ 收件箱游标 + ack |
| `ai.generate` | `generate` | 关 | ✅ |
| `game.launch` | `launchGame` | 关 | ✅ |
| `external.fetch` | `service` / `serviceImage` | 关 | ✅ 只读连接器工具 + URL 前缀 + 缓存 |
| `events.emit` | `emitEvent` | 关 | ✅ 出站 Webhook（v42，至少一次 + HMAC 签名） |
| `schedules.manage` | — | 关 | ⛔ 预留；应用级调度目前由 Dashboard/API 管理，未开放给页面 SDK |

**身份模型 v3 与现有授权的边界（2026-11-22）**：

| 层 | 现状 | 说明 |
|---|---|---|
| `users.kind` | ✅ `member` / `guest` | `UserManager` 只加载 member；guest 不能登录、SSO/LDAP、邀请注册；`PrincipalDirectory` 解析 guest |
| `light_app_tables.member_visible` | ✅ 参与授权 | `participate` 只能读 `member_visible=1`；隐藏表与不可见行统一 404 |
| `light_app_rows.row_owner_user_id` | ✅ 参与授权 | `row_scope=user` 按 owner 过滤；兼容旧行；`mask_unless_own` 行级 PII |
| guest / `participate` | ✅ P1 | guest 会话、app token、`/join`、`/my`、邀请 / 加入码、host policy |
| app-scoped JWT（D3） | ✅ | 2 小时，仅该 app 路由与只读启动接口 |
| IM 身份绑定（PR-11） | ✅ | `account_key` + `sender_id`、`ToolScope`；app 数据工具在 IM 内要求 owner 私聊 |
| submission / 匿名公开填报 | ⛔ | P3，不在当前范围 |

因此，当前权限边界是：**平台成员 / 协作者 / 受邀 guest 的 ACL + 表可见性 + 行归属 + PII**；不要对外承诺匿名公开填报，或 IM 外部 sender 的数据工具能力。

### 3.7 沙箱模型

App HTML 是 Agent 生成内容，按不可信代码处理：

1. **写入时**：`validate_html` 拒绝远程资源、网络 API、导航 API、frame/object/embed、WebRTC、`javascript:` URL；已有旧应用若静态含 frame，渲染时直接显示安全错误页。
2. **渲染时**：
   - 外层固定 wrapper 文档由 Octop 自己编写，声明 `frame-src 'none'`；App 在 wrapper 的 inner iframe 中，opaque origin。
   - App 文档自身不写显式 `frame-src`：Chrome 会因此拒绝创建它自己的 srcdoc frame（表现为整页空白）；`default-src 'none'` 仍隐含禁止嵌套 frame。
   - wrapper 负责双向转发；第二次 `load` 视为导航逃逸，移除 inner frame 并向宿主发 `escaped`。
   - App 脚本之前注入 `WEBRTC_FREEZE`：RTC 全局不可配置 `undefined`；封堵 frame 创建 API；MutationObserver 兜底。
   - SDK 每次桥请求前自检 RTC/frame 不变量，失败拒绝请求并上报。
3. **宿主桥**：只接受来自 wrapper/contentWindow 的消息；`event.source` 校验；未知方法拒绝；能力位检查。
4. **公开页**：服务端 `build_app_doc` + `build_wrapper_doc`，使用同一套 CSP/冻结；只读桥仅支持 `tables` / `schema` / `rows`。
5. **残余风险**：srcdoc iframe 与 Dashboard 同 renderer process，死循环仍可卡死页面；浏览器 bug/未覆盖 DOM API 可能绕过；这是纵深防御，不是形式化隔离。需要强隔离时应走独立内容域或服务端浏览器。

### 3.8 并发、幂等与限额

- **行追加**：DB 事务内单行 INSERT + 计数更新；进程内 `asyncio.Lock` 仍包住同一应用的行操作，跨进程依靠 DB 事务、唯一索引和 CAS。
- **行修改/删除**：`row_id + version` CAS；冲突 409 `APP_CONFLICT`。
- **幂等**：`client_request_id`；数据源 `(source_id, source_key)`；游戏 `session_id:op:row`。
- **多实例**：插入/更新/CAS 正确；但限额检查（如 `count >= MAX_ROWS`）仍是 check-then-insert，且限流/锁是进程内，不是全局。
- **懒迁移**：进程内锁 + `storage_version` 条件更新；其他进程完成后重读 DB。

### 3.9 前端结构

| 路径 | 职责 |
|---|---|
| `dashboard/src/pages/Apps/index.tsx` | 工作台：Board 列表、网格、创建入口、备份恢复、归档列表 |
| `dashboard/src/pages/Apps/BoardGrid.tsx` | 12 列网格、拖拽/缩放/锁定/撤销/自动排列 |
| `dashboard/src/pages/Apps/BoardView.tsx` | 全屏看板 |
| `dashboard/src/pages/Apps/Detail.tsx` | 应用详情：界面/数据双 Tab、助手、分享、发布、能力、数据源、备份 |
| `dashboard/src/pages/Apps/CreateMiniAppModal.tsx` | 创建弹窗：快速 / 模板 / 云端市场占位 |
| `dashboard/src/pages/Apps/appBoard.ts` | 旧 localStorage Board 迁移与放置逻辑 |
| `dashboard/src/pages/Apps/DesktopGrid.tsx` | AgentOS 桌面：专家工作空间、窗口管理、启动动效、桌宠 |
| `dashboard/src/pages/Apps/desktopWindows.ts` | Window Registry、窗口工厂、会话恢复与标题解析 |
| `dashboard/src/pages/Apps/DesktopDock.tsx` | Dock 2.0：运行窗口、全部应用、右键窗口操作 |
| `dashboard/src/pages/Apps/DesktopMissionControl.tsx` | Mission Control：跨桌面窗口总览 |
| `dashboard/src/pages/Apps/DesktopActivityCenter.tsx` | 智能体动态、打开窗口与待处理提醒 |
| `dashboard/src/pages/Apps/DesktopNotificationCenter.tsx` | 通知中心（审批、访问申请、任务、安全） |
| `dashboard/src/pages/Apps/DesktopCommandPalette.tsx` | `Cmd/Ctrl+K` 全局命令面板 |
| `dashboard/src/pages/Apps/DesktopManageDrawer.tsx` | 桌面管理：隐藏、文件夹、Dock 固定 |
| `dashboard/src/pages/Apps/desktopOrg.ts` | 桌面组织偏好（localStorage） |
| `dashboard/src/pages/Apps/DesktopChatWindow.tsx` | 桌面内专家会话窗口 |
| `dashboard/src/pages/Apps/DesktopWebView.tsx` | 桌面内外链 web-view 窗口（含 HTML / 浏览器窗口） |
| `dashboard/src/components/MiniAppFrame/index.tsx` | 通用 MiniApp 宿主（聊天/侧栏） |
| `.../wrapper.ts` | 双层 wrapper 文档 |
| `.../bridge.ts` | 共享桥 + 能力检查 + 违规上报 |
| `.../sdk.ts` | `window.octopApp` SDK + WebRTC 冻结 + `buildSrcDoc` |
| `.../capabilities.ts` | 前端能力目录与方法映射 |
| `.../GameFrame.tsx` | 外部游戏侧栏宿主 |
| `dashboard/src/pages/Chat/components/ChatDockSessionApps.tsx` | 对话内 MiniApp 列表/打开 |
| `dashboard/src/plugins/toolRenderers/builtin/AppCard.tsx` | 对话 app_card |
| `dashboard/src/plugins/toolRenderers/builtin/AppInspectCard.tsx` | Excel 检查确认卡 |
| `dashboard/src/api/modules/apps.ts` / `boards.ts` | API 客户端 |

---

## 4. 实现现状矩阵

### 4.1 功能矩阵

| 域 | 能力 | 状态 | 说明 |
|---|---|---|---|
| 创建 | Agent 工具创建 | ✅ | `app_create` + `app_write_html` |
| 创建 | Excel 快速模式 | ✅ | 确定性渲染，不打 LLM |
| 创建 | 系统模板 | ✅ | 6 个：客户跟进、月度开支、库存盘点、项目任务、费用报销、内容审核 |
| 创建 | 专家模板 | ✅ | `app_templates/<id>/`，随专家发布/安装 |
| 创建 | 云端市场 | ⛔ | 前端占位 |
| 数据 | 多表 | ✅ | 最多 12 张 |
| 数据 | 行入库 + CAS + 幂等 | ✅ | `light_app_rows` |
| 数据 | 旧 CSV 懒迁移 | ✅ | `storage_version` |
| 数据 | Schema 改名/删除/改类型 | ✅ | `app_alter_schema` |
| 数据 | CSV/Excel/JSON 导入 | ✅ | sheet/header/dry-run/upsert/多文件/失败行 |
| 数据 | CSV 导出 | ✅ | 从 DB 渲染 |
| 数据 | 备份/恢复 | ✅ | zip v3（`row_ids.json` + 表配置 + 集成声明；无 secret；恢复后 disabled），导入前依赖预检，64 MB / 256 MB 上限 |
| 数据 | 引用列 | ✅ | `reference` + 写入校验 + 删除/replace 守卫 + `expand_refs` label |
| 数据 | 工作区文件数据源 | ✅ | 手动/定时 |
| 数据 | 只读连接器数据源 | ✅ | 外部 MCP 工具 + 游标 + 退避 |
| 数据 | 外部服务 `light_app_services` | 🟡 | 声明/CRUD/只读调用/TTL 缓存/图片资产/公开缓存调用/Agent 工具已实现；promote 待做 |
| 界面 | 单文件 HTML + SDK | ✅ | 19 个顶层方法（含 `aggregate` / `updateRow` / `updateRows` / `deleteRow` / `onDataChange` / `events` / `emitEvent` / `service`；`service` 含 `call` / `callDetailed` / `image`） |
| 界面 | 能力清单 | ✅ | 10 执行 + 1 预留（`schedules.manage`） |
| 界面 | 双层沙箱 + CSP + WebRTC 冻结 | ✅ | P0/P1/WebRTC 已完成 |
| 界面 | 违规上报 | ✅ | 登录态页面；公开页暂不上报 |
| 协作 | 应用 ACL | ✅ | view / edit_data / edit_structure |
| 协作 | 协作者 | ✅ | 最多 50 |
| 协作 | 应用交接 | ✅ | 复制文件 + 更新 agent |
| 协作 | 删除守卫 | ✅ | `AGENT_HAS_APPS` |
| 协作 | 团队交付物 = 应用 | ✅ | `artifact_type="app"` |
| 协作 | 身份与访问（v3 P0/P1） | ✅ | `PrincipalDirectory` / `authorize()` / 行级 PII / 成员 / guest / host policy（PR-01~10） |
| 协作 | guest / `participate` | ✅ | `/join`、`/my`、行级 PII；匿名公开填报仍为 P3 |
| 协作 | IM 外部 sender 身份 / `/link` | ✅ | PR-11/12：`account_key`/`sender_id`、数据工具收口、私聊个人入口码与 `/link` |
| 发布 | 只读公开链接 | ✅ | token hash + 撤销/过期 |
| 发布 | 发布快照 | ✅ | HTML + 公开表结构；行实时 |
| 发布 | 公开表可见性 | ✅ | 主表默认公开，附加表默认私有 |
| 发布 | PII 脱敏 | ✅ | 列级 + 值级 |
| 发布 | HTML 上一版回滚 | ✅ | `previous_html` |
| 发布 | 公开看板 | ⛔ | R2/P3 未实现 |
| AI | 只读数据助手 | ✅ | 工具循环 + 图表 + SSE |
| AI | 一次性文本生成 | ✅ | `ai.generate` + owner 额度 |
| AI | 按应用选择模型 | ⛔ | 目前用 owning agent 默认模型 |
| AI | 实时数据刷新 `data_version` 轮询 | ✅ | 宿主 5 秒轮询；`onDataChange` / `octop:data-changed` |
| 游戏 | 外部游戏接入 | ✅ | 白名单 + 2h token + 结果回写 |
| 看板 | 个人多 Board | ✅ | 12 列、CAS、锁定、撤销、全屏、自动排列 |
| 看板 | 公开看板分享 | ⛔ | 无 `share_token_hash` / `/public/boards/*` |
| IM | `app_share_link` | ✅ | 一次调用、复用公开链接、封面；已绑定 owner 的私聊返回 app-scoped 免密链接 |
| IM | `app_open` 通道降级 | ✅ | dashboard → app_card；IM → 公开链接 / 文本降级 |
| IM | 微信纯文本格式化 | ✅ | `format_channel_text` |
| IM | 单轮预算/重复失败熔断 | ✅ | `StreamBudget` |
| IM | 渠道身份绑定 | ✅ | D4-lite：Dashboard 绑定码 + `/bind`；仅 owner 本人绑定生效，群聊不发个人链接 |
| 事件 | 事件平面驱动 `app.refresh` / `app.import` / `app.inbox` | ✅ | 收件箱游标 + ack；`app.import` 幂等 append；触发器已接 |
| 事件 | 应用收件箱 / `emitEvent` | ✅ | 收件箱游标 + ack；出站 Webhook（v42）：owner 声明 URL/事件类型，HMAC 签名、outbox 重试 |
| 分发 | 跨主机 Bundle v2 | ⛔ | 只有同实例 zip 备份/恢复 |
| 运维 | 指标 | ✅ | 应用/行/导出/助手/生成/公开/CSP/沙箱 |
| 运维 | 审计 | ✅ | 快速创建、分享、发布、导出、备份/恢复、游戏、CSP/沙箱、生成；行写入进 `light_app_row_history`（v40），schema/HTML 写入仍只靠版本与备份 |
| 运维 | 限流 | ✅ | 每进程；非全局 |

### 4.2 Agent 工具（42 个）

| 工具 | 权限 | 作用 |
|---|---|---|
| `app_inspect_file` | Agent 可访问 | 检查工作区表格文件 |
| `app_propose_plan` | Agent 可访问 | 生成 setup plan（不创建应用） |
| `app_get_plan` | Agent 可访问 | 读取 plan 状态 / 预检结果 |
| `app_cancel_plan` | Agent 可访问 | 取消 draft plan |
| `app_list` | Agent 可访问 | 列出当前用户在该 Agent 下创建的应用 |
| `app_list_bindings` | `view` | 列出内置数据集与 connector/external 服务绑定（只读） |
| `app_add_service` | owner/admin | 声明只读外部服务（工具白名单、URL 前缀、缓存/限流） |
| `app_list_services` | `view` | 列出外部服务声明 |
| `app_remove_service` | owner/admin | 删除外部服务声明及缓存 |
| `app_open` | `view` | 打开应用；dashboard 返回 `app_card`，IM 返回公开链接 |
| `app_share_link` | owner/admin | 获取/轮换公开链接 + 封面；已绑定 owner 的私聊返回个人免密链接 |
| `app_create` | Agent 可访问 | 建应用 + 主表 + 可选能力清单 |
| `app_create_from_template` | Agent 可访问 | 从系统/专家模板建空应用 |
| `app_add_table` | `edit_structure` | 新增附加表 |
| `app_update_schema` | `edit_structure` | 追加列（只能加） |
| `app_alter_schema` | `edit_structure` | 改名/删除/改类型/标 PII |
| `app_set_table_visibility` | owner/admin | 设置表是否公开 |
| `app_set_capabilities` | owner/admin | 替换页面能力清单（仅低风险能力） |
| `app_write_html` | `edit_structure` | 写完整 HTML（不产生聊天卡片） |
| `app_read_data` | `view` | 分页读行 + `row_id`/`version` |
| `app_append_row` | `edit_data` | 追加一行（可幂等） |
| `app_update_row` | `edit_data` | 按 `row_id` + `version` 更新 |
| `app_delete_row` | `edit_data` | 按 `row_id` + `version` 删除 |
| `app_import_rows` | `edit_data` | 批量导入（dry-run/upsert/多文件） |
| `app_save_asset` | `edit_data` | 保存图片素材 |
| `app_set_preview` | `edit_data` | 保存 SVG 封面 |
| `app_integration_list` | owner/admin | 只读：入站 source/订阅、出站 Webhook、调度、外部服务清单 |
| `app_integration_health` | owner/admin | 只读：源错误/停滞、mapping 漂移、目标权限、dead/failed 投递、失败调度 |
| `app_integration_trace` | owner/admin | 只读：按内部/外部 event id 追踪 source → delivery → 收件箱/写入行 |
| `app_integration_create_ingress` | owner/admin | 创建 app 自有入站入口；返回 hook_url + 一次性密钥（**强制 HITL**） |
| `app_integration_update_ingress` | owner/admin | 改入口模式/事件类型/mapping/限流/启停（**强制 HITL**） |
| `app_integration_delete_ingress` | owner/admin | 删除入口及其 source/subscription（**强制 HITL**） |
| `app_integration_test_ingress` | owner/admin | 发送测试事件并交给 worker（**强制 HITL**） |
| `app_integration_create_webhook` | owner/admin | 创建出站 Webhook；secret 不返回 Agent（**强制 HITL**） |
| `app_integration_update_webhook` | owner/admin | 改出站 Webhook 名称/URL/事件类型/启停（**强制 HITL**） |
| `app_integration_delete_webhook` | owner/admin | 删除出站 Webhook 及其 source/subscription（**强制 HITL**） |
| `app_integration_test_webhook` | owner/admin | 发送出站测试事件并交给 worker（**强制 HITL**） |
| `app_delete` | owner/admin | 删除应用（**强制 HITL**） |
| `app_run_task` | `view` + 任务内行级授权 | 发起 `summarize_row` / `table_digest` / `pre_review` AI 任务；需 `ai.agent_task` 能力位 |
| `app_get_task` | `view` | 读取 AI 任务状态、结果与错误 |
| `app_list_tasks` | `view` | 列出应用最近的 AI 任务 |
| `app_cancel_task` | 创建者/owner | 取消排队任务，或标记运行中任务 |

说明：团队 host 不挂这组工具；它们只挂普通 Agent（`if not team_host`）。

### 4.3 API 面

应用（`/api/apps`）：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/apps` | 列表 |
| GET | `/apps/in-thread` | 对话内应用 |
| GET | `/apps/templates` | 模板列表 |
| GET | `/apps/templates/{template_id}/preview` | 模板封面 |
| POST | `/apps/from-template` | 从模板创建 |
| POST | `/apps/quick/inspect` | 快速模式分析文件 |
| POST | `/apps/quick/create` | 快速模式创建 |
| GET | `/apps/setup-plans` | 管理员列出 setup plans |
| GET | `/apps/setup-plans/{plan_id}` | 读取计划状态/预检结果 |
| POST | `/apps/setup-plans/{plan_id}/confirm` | 确认计划（创建空应用并 dry-run） |
| POST | `/apps/setup-plans/{plan_id}/execute` | 执行确认后的导入 |
| POST | `/apps/setup-plans/{plan_id}/cancel` | 取消计划 |
| GET | `/apps/{id}` | 详情 |
| GET | `/apps/{id}/tables` | 表列表（含 `data_version`） |
| GET | `/apps/{id}/data-version` | 表级 `data_version`（实时刷新轮询） |
| GET | `/apps/{id}/dataset` | 内置数据集服务描述（`builtin.dataset`） |
| POST | `/apps/{id}/dataset/query` | 内置数据集只读查询 |
| GET | `/apps/{id}/bindings` | 服务绑定列表（含内置数据集） |
| PUT/DELETE | `/apps/{id}/bindings/{binding_id}` | 保存/删除服务绑定 |
| GET | `/apps/{id}/services` | 外部服务声明列表 |
| PUT/DELETE | `/apps/{id}/services/{name}` | 声明/删除外部服务（owner/admin） |
| POST | `/apps/{id}/services/{name}/call` | 调用声明的只读工具（需 `external.fetch`） |
| POST | `/apps/{id}/services/{name}/image` | 拉取图片并存入 assets |
| GET | `/apps/{id}/events` | 事件收件箱（游标） |
| POST | `/apps/{id}/events/ack` | 标记事件已处理 |
| POST | `/apps/{id}/events/emit` | 发出一条出站事件（需 `events.emit`） |
| GET/POST | `/apps/{id}/webhooks` | 出站 Webhook 列表/创建（owner/admin） |
| PATCH/DELETE | `/apps/{id}/webhooks/{webhook_id}` | 改/删出站 Webhook（可轮换密钥） |
| GET/POST | `/apps/{id}/ingress` | app-owned 入站入口列表/创建（owner/admin） |
| PATCH/DELETE | `/apps/{id}/ingress/{ingress_id}` | 改/删入站入口 |
| POST | `/apps/{id}/ingress/{ingress_id}/rotate-secret` | 轮换入站密钥 |
| POST | `/apps/{id}/ingress/{ingress_id}/test` | 发送入站测试事件 |
| GET | `/apps/{id}/dataset/export` | 导出内置数据集（schema/rows/rules/bindings） |
| POST | `/apps/dataset/import` | 从导出创建一个新应用 |
| PATCH | `/apps/{id}/tables/{table_key}` | 表可见性 / `row_scope` |
| PUT | `/apps/{id}/tables/{table_key}/rules` | 声明式表规则（状态机 + 收件箱触发器） |
| GET | `/apps/{id}/html` | HTML |
| POST | `/apps/{id}/security-events` | CSP/沙箱违规上报 |
| GET | `/apps/{id}/files` | 文件列表 |
| GET/POST | `/apps/{id}/assets` | 素材列表/上传 |
| GET | `/apps/{id}/assets/{name}` | 素材读取 |
| GET | `/apps/{id}/preview` | 封面 |
| GET/POST | `/apps/{id}/rows` | 读行/追加（GET 支持 `expand_refs=true` 返回引用 label） |
| PATCH/DELETE | `/apps/{id}/rows/{row_id}` | 改行/删行（CAS） |
| GET | `/apps/{id}/rows/{row_id}/history` | 行级变更历史 |
| GET | `/apps/{id}/aggregate` | 服务端分组聚合 |
| GET | `/apps/{id}/export` | CSV 导出 |
| GET | `/apps/{id}/backup` | 备份 zip |
| POST | `/apps/restore/preflight` | 恢复前扫描依赖与归档，返回阻断错误/警告 |
| POST | `/apps/restore` | 恢复备份（内部先做预检） |
| PATCH | `/apps/{id}/schema` | 结构变更 |
| PATCH | `/apps/{id}` | 改名/描述 |
| PUT | `/apps/{id}/capabilities` | 能力清单 |
| POST | `/apps/{id}/restore-html` | 恢复上一版 HTML |
| DELETE | `/apps/{id}` | 删除 |
| GET/PUT | `/apps/{id}/shares` | 协作者 |
| GET/POST/DELETE | `/apps/{id}/publication` | 公开链接状态/创建/撤销 |
| POST | `/apps/{id}/publication/publish` | 刷新发布快照 |
| POST | `/apps/{id}/transfer` | 交接给另一个 Agent |
| GET | `/apps/{id}/assistant/history` | 助手历史 |
| POST | `/apps/{id}/assistant` | 助手问答（SSE） |
| POST | `/apps/{id}/generate` | 一次性生成 |
| POST | `/apps/{id}/upload` | 上传导入文件 |
| POST | `/apps/{id}/tables/{table_key}/import` | 导入 |
| GET | `/apps/{id}/sources` | 数据源列表 |
| PUT/DELETE | `/apps/{id}/sources/{source_id}` | 保存/删除数据源 |
| POST | `/apps/{id}/sources/{source_id}/run` | 立即运行 |
| GET | `/apps/{id}/source-tools` | 可用的只读连接器工具 |
| GET/POST | `/apps/{id}/schedules` | 列出/创建应用级调度 |
| PATCH/DELETE | `/apps/{id}/schedules/{schedule_id}` | 修改/删除调度 |
| GET | `/apps/{id}/task-types` | 已注册的 AI 任务类型（tier / write_mode） |
| GET/POST | `/apps/{id}/tasks` | 列出/创建 AI 任务 |
| GET | `/apps/{id}/tasks/{task_id}` | AI 任务状态与结果 |
| POST | `/apps/{id}/tasks/{task_id}/cancel` | 取消 AI 任务 |
| POST | `/apps/{id}/game-sessions` | 创建游戏会话 |

认证与访问（`/api/auth`、`/api/apps`）：

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/auth/app-access/preview` | 预览一次性链接 |
| POST | `/auth/app-access/exchange` | 换取 app-scoped JWT |
| POST | `/auth/channel-bindings/code` | 生成 IM 绑定码 |
| GET/DELETE | `/auth/channel-bindings` | 列出/解绑 IM 身份 |
| POST/GET | `/apps/access-requests` | 发起/列出访问申请 |
| POST | `/apps/access-requests/{request_id}/approve` | 批准申请 |
| POST | `/apps/access-requests/{request_id}/reject` | 拒绝申请 |

公开面（`/api/public/apps`）：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/public/apps/{token}` | 公开应用元数据 + HTML + 表 |
| GET | `/public/apps/{token}/rows` | 公开行（只读、脱敏、快照列过滤） |
| GET | `/public/apps/{token}/assets/{name}` | 公开素材 |
| POST | `/public/apps/{token}/services/{name}/call` | 公开只读服务（只查缓存） |
| GET | `/p/{token}` | 服务端渲染宿主页（JWT 豁免） |

看板与游戏：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET/POST | `/api/boards` | 列表/创建 |
| GET/PATCH/DELETE | `/api/boards/{board_id}` | 详情/改名/删除 |
| PUT | `/api/boards/{board_id}/layout` | CAS 保存布局 |
| POST | `/api/game-sessions/{token}/results` | 游戏结果写入（JWT 豁免） |

### 4.4 路线图状态

依据 `docs/miniapp-platform-roadmap.md`：

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **R1** | 能力清单、发布快照、composeMessage、限流、HTML 回滚、多 Board | ✅ 全部落地（个人看板部分） |
| **R2** | 实时外部服务、事件收件箱/发送、统一调度、公开看板 | ◐ 外部服务、事件收件箱、出站 Webhook、应用级调度已落地；公开看板未做 |
| **R3** | 迁移包 v2、IM 公开链接、owner 身份绑定 | ◐ IM 公开链接、D4-lite 与 PR-11/12 sender 身份 / `/link` 已实现；迁移包 v2 未做 |

R2/R3 的共同地基（能力清单）已经就位：`external.fetch` 与 `events.emit` 已接入执行链路，`schedules.manage` 仍是可声明、不可执行的能力名，避免未来端点默认放行。

**身份 v3 状态（2026-11-22）**：P0/P1 已落地并部署（PR-01~PR-10，schema v47）：`PrincipalDirectory`、`authorize()`、路由 action 注册表、行级 PII、成员 / 邀请 / 加入码、guest 会话与门户、host policy、Dashboard 成员页。P2a（`account_key` / `sender_id`、`ToolScope`、IM 数据工具收口、`/link`）已实现并部署；submission / per-sender actor 仍属 P2b/P3。

### 4.5 设计文档落地情况

| 文档 | 文档声明 | 代码现状 |
|---|---|---|
| `light-apps-architecture.md` | 已改为“历史设计稿（主体已实施）” | 行入库、CAS、幂等、懒迁移、发布、MCP 数据源、可编辑数据表、CSV 导入导出、`data_version` 实时刷新已落地 |
| `light-apps-enhancement-plan.md` | 已改为“讨论稿（主体已实施）” | P0/P1/P2/P3 主项均落地，遗留按应用选择模型 |
| `light-apps-pii-design.md` | 策略文档 | ✅ 一致 |
| `light-apps-games.md` | 已实现 | ✅ 一致 |
| `miniapp-creation-modes-design.md` | P0/P1 已实现，云端市场 P2 未做 | ✅ 一致；市场仍是占位 |
| `miniapp-im-optimization.md` | P0 已落地 | ✅ 一致；P1/P2 部分未做 |
| `miniapp-sandbox-hardening.md` | P0/P1/WebRTC 已实施 | ✅ 一致；13 个浏览器回归通过 |
| `miniapp-platform-roadmap.md` | R1 / R2.0 已实施；R2.1–R2.3 部分/核心实施 | ◐ 部分一致；以 roadmap 状态行为准 |
| `miniapp-event-api-design.md` | 收件箱 + 出站 Webhook 已实施 | ◐ 部分一致；`app.inbox` + 游标/ack、`events.emit` + outbound webhook 已落地，CloudEvents/ordering 未做；已知缺口见 [event-model.md](./event-model.md) |
| `miniapp-identity-access-plan.md` | v3 身份/访问设计（2026-11-21） | ✅ P0/P1/P2a 已实现（v48，PR-01~12） |
| `miniapp-identity-implementation-plan.md` | P0–P2a 实施拆解 | ✅ PR-01~PR-12 已实现；PR-01~PR-10 已部署 |
| `docs/architecture-overview.md` | 仍是 2026-11-08 旧数据（35 个工具、50 张表、v29） | ⬜ 待同步；以本文为准 |

> 已删除两份已实施且被本文覆盖的设计稿：`light-apps-multi-table-design.md`、`light-apps-assistant-and-import-design.md`；多表、助手与导入内容见 §2.2、§2.6、§2.7。

---

## 5. 应用场景与落地

> 这一节回答两个问题：MiniApp 适合做什么、从哪一步开始。功能细节见 §2；未落地能力见 §4.4 / §7。

### 5.1 适用判断

MiniApp 适合“结构化数据 + 轻界面 + 可分享交付”的场景。满足以下条件时优先使用：

| 判断项 | 适合 | 不适合 |
|---|---|---|
| 数据形态 | 1–12 张表，单表 ≤2 万行 / 5 MB | 高频交易、超大宽表、跨库事务 |
| 读写比例 | 读多写少；批量导入 / 人工维护 / 外部事件写入 | 高并发写入、强一致账务 |
| 交付形态 | 台账、看板、进度板、只读页面 | 复杂审批流、实时协同编辑 |
| 用户 | 平台成员 / 协作者 / 受邀 guest（邀请、加入码）；IM 私聊可由绑定 guest 拿个人入口 | 匿名公开填报；群聊内仍不开放 app 数据工具 |
| 迭代方式 | 业务方随改随用，Agent 重写 HTML | 需要传统前端排期的重交互应用 |

### 5.2 典型场景

| 场景 | 典型形态 | 现在可用的能力 | 当前边界 |
|---|---|---|---|
| **A. 台账与填报** | 门店 / 巡检 / 客户跟进表，按人可见、状态流转 | 快速创建、`row_scope=user`、状态机规则、页面写行、行级历史 | 填报者必须是平台用户 / 协作者；公开填报未做 |
| **B. 轻看板与摘要卡** | Excel → 看板 / 台账 / 进度板；公开链接；工作台格子 | 快速模式三视图、服务端聚合与图表、发布快照、Board / 桌面 | 公开看板未做；快速模式图表类型有限 |
| **C. 外部数据速览** | 豆瓣读书、公开 JSON / 文本、连接器数据 | R2.1 外部服务（只读工具、URL 前缀、TTL 缓存、限流） | 只读；不能写回外部系统；builtin→external promote 未做 |
| **D. 审核与内容流转** | 审片 / 质检 / 合规：列表 → 预览 → 通过 / 驳回 → 留痕 | 状态机、行级历史、页面写行、导出、ACL | 页面不能上传文件，同源视频播放未做；不能替代剪辑 / 工作流 |
| **E. 对外只读发布** | 政策 / 价目 / 名单 / 结果 / 活动页 | 发布快照、`public_visible`、PII 脱敏、限流、撤销 | 只读；公开事件 / 公开看板不支持 |
| **F. 外部系统“最后一公里”** | CRM / 工单 / 订单数据落地成业务页面 | 入站 Webhook、出站 Webhook、数据源定时同步、应用级调度 | 不是双向实时集成；无 workflow runner |
| **G. 个人 / 团队轻工具** | 番茄钟、报名表、资料目录、外部游戏 | 单文件 HTML、SDK、能力清单、游戏 token | 适合轻量工具，不适合重前端应用 |

A–D 与 `docs/miniapp-product-plan.md` 的四个高频场景一致；本表补充了当前边界和 E–G 的交付型场景。

### 5.3 落地路径

| 阶段 | 目标 | 关键动作 | 入口 |
|---|---|---|---|
| **0. 选场景** | 选一个 1–5 天能验收的场景 | 已有 Excel / 外部系统数据；每周有人看 / 改；交付物是一张表或链接 | — |
| **1. 定数据与权限** | 表、字段、唯一键、状态、PII、协作者 | 标 PII；定 `row_scope`；定 `view` / `edit_data` / `edit_structure` | 应用详情 / `PUT /apps/{id}/shares` |
| **2. 快速创建** | 30 分钟得到可用页面 | 上传 Excel → inspect → 确认字段 → 选视图 | `POST /apps/quick/inspect`、`POST /apps/quick/create` |
| **3. Agent 精修** | 让页面贴合业务 | 补表 / 改 schema / 画页面 / 导入数据 | `app_add_table`、`app_alter_schema`、`app_write_html`、`app_import_rows`、`app_set_preview` |
| **4. 协作与发布** | 让人用起来 | 加协作者；发布快照；公开链接；IM 免密链接 | `/shares`、`/publication`、`app_share_link` |
| **5. 自动化与集成** | 减少手工 | 数据源定时同步；公开快照刷新；入站 / 出站 Webhook；外部服务 | `/sources`、`/schedules`、`/ingress`、`/webhooks`、`/services` |
| **6. 运维与验收** | 可持续 | 备份 / 恢复预检；行历史；页面回滚；审计；限额监控 | `/backup`、`/restore/preflight`、`/rows/{row_id}/history`、`/restore-html` |

**30 分钟最小闭环**：

1. 上传一份真实 Excel；
2. 确认字段，生成台账 / 看板；
3. 改一行数据，确认 `data_version` 刷新；
4. 发布只读链接，手机打开；
5. 问应用内助手一个汇总问题；
6. 加一个 `view` 协作者。

这条链路验证了“导入 → 界面 → 刷新 → 分享 → AI → 权限”。

### 5.4 验收指标

| 指标 | 建议口径 |
|---|---|
| 首次可用时间 | 从上传文件到可分享页面 ≤1 天 |
| 数据新鲜度 | 手工更新 ≤1 天 / 定时同步按业务要求 |
| 使用 | 周活跃查看人数、每周写入行数 |
| 效率 | 节省的人工汇总 / 整理小时数 |
| 质量 | 返工率、错误行比例、PII / 权限事故数 |
| 集成 | Webhook 成功率、调度失败 / 积压、公开链接 429 比例 |

### 5.5 角色分工

| 角色 | 负责 |
|---|---|
| 业务 owner | 字段、指标、状态流转、验收口径 |
| IT / 管理员 | 连接器与 `public_base_url`、IM 应用、备份、PostgreSQL / 对象存储 |
| Agent builder | 快速创建、页面 HTML / SDK、能力清单、调度与 Webhook |
| 安全 / 合规 | PII 标记、公开范围、协作者权限、审计与导出策略 |

### 5.6 常见坑

- 把 MiniApp 当 ERP / CRM 权威源；核心交易应留在原生系统。
- 不标 PII 就导入 / 发布；公开读取虽脱敏，但源头仍应最小化。
- 期待公开填报 / 公开看板；当前公开链接只读，外部写入要签名 Webhook。
- 单表超过 2 万行 / 5 MB 才拆表；应在建模时按时间 / 业务分区拆表。
- 改了 HTML 忘记重新发布，公开链接仍是旧快照。
- 把 `edit_structure` 当普通编辑权限；它会改 schema / HTML。
- 用页面过滤当权限控制；公开面只认 `public_visible` 和公开表。
- 把公开链接当成成员权限；公开面只认 `public_visible`，成员表可见性与行级 PII 只在登录 / 成员路径生效。

---

## 6. 验证

### 6.1 本次运行

```bash
# PR-10 定向验证（2026-11-22）
uv run pytest tests/unit/apps tests/unit/db/test_app_members_migration.py \
  tests/unit/db/test_db_pool.py -q          # 529 passed
uv run pytest tests/integration/test_apps_api.py \
  tests/integration/test_app_members_api.py \
  tests/integration/test_app_host_policy_api.py \
  tests/integration/test_guest_portal_api.py -q   # 97 passed
uv run pytest tests/unit/apps/test_members.py -q  # 10 passed
```

前端：`src/api/request.appTokens.test.ts` + Join / Guest / MembersPanel / Detail 共 32 个用例通过；`tsc -b`、ESLint、生产构建通过。线上服务已部署并迁移到 schema v47（`/api/health` `ok:true`，新路由已注册）。

PR-11/12（2026-11-22）：
```bash
uv run pytest tests/unit/i18n tests/unit/auth tests/unit/gateway tests/unit/agents -q
# 1879 passed, 1 skipped
uv run pytest tests/unit/apps -q          # 505 passed
uv run pytest tests/unit/db -q            # 245 passed
uv run pytest tests/integration/test_apps_api.py \
  tests/integration/test_app_members_api.py \
  tests/integration/test_app_host_policy_api.py \
  tests/integration/test_guest_portal_api.py \
  tests/integration/test_app_access_api.py \
  tests/integration/test_channel_bindings_api.py -q   # 103 passed
uv run pytest tests/unit/backup/test_system_archive.py -q  # 22 passed
```
`make lint`、`make typecheck` 通过；前端新增 Guest `/link` 码弹窗用例。

未运行：一次完整的 `make all`。PR-01~10 的全量 pytest 在本机跑到 88% 时因内存 / swap 压力被中止；PR-11/12 使用定向测试 + lint + mypy。合并前应在 CI 或停服窗口补跑。

### 6.2 测试分布

| 范围 | 覆盖点 | 用例数 |
|---|---|---:|
| 单元：应用核心 | `test_service.py` | 135 |
| 单元：模型/校验 | `test_models.py` | 62 |
| 单元：工具/风险/预检 | `test_tools.py`、`test_tool_policy.py`、`test_hitl_preflight.py` | 47 |
| 单元：沙箱 | `test_sandbox.py` | 19 |
| 单元：看板 | `test_boards.py` | 16 |
| 单元：图表/分析/聚合 | `test_charts.py`、`test_inspect.py`、`test_aggregate.py` | 30 |
| 单元：封面/助手 | `test_preview.py`、`test_assistant.py` | 25 |
| 单元：导入/快速渲染 | `test_importer.py`、`test_viewgen.py` | 20 |
| 单元：生成/游戏 | `test_generation.py`、`test_game_sessions.py` | 14 |
| 单元：PII/限流/数据源 | `test_pii.py`、`test_limits.py`、`test_sources.py` | 17 |
| 单元：会话/提示/能力/调度 | `test_session_apps.py`、`test_prompt.py`、`test_capabilities.py`、`test_scheduler.py` | 29 |
| 单元：引用列 | `test_references.py` | 7 |
| 单元：setup plans | `test_setup_plans.py` | 15 |
| 单元：集成/Webhook/Ingress | `test_integration_tools.py`、`test_webhooks.py`、`test_ingress.py` | 27 |
| 单元：访问链接 | `test_access_links.py` | 2 |
| 集成：应用 API | `test_apps_api.py` | 77 |
| 集成：看板 API | `test_boards_api.py` | 5 |
| 单元：DB / schema | `tests/unit/db/test_db_pool.py` | 22 |
| **合计** | `tests/unit/apps` + `test_db_pool.py` + 两个集成文件 | **569** |
| 集成：浏览器沙箱 | `test_miniapp_sandbox.py`（单独运行） | 13 |

> 上表是 2026-11-21 基线。PR-06~PR-10 新增的 `test_pii_policy` / `test_members` / `test_host_policy` / `test_guest_sessions` / 成员与 guest API 集成用例未计入；最新一次定向结果为 529 单测 + 97 集成，见 §6.1。

### 6.3 手工验证建议

1. 工作台「创建」→ 上传多 sheet Excel → 确认字段 → 生成看板 → 数据 Tab 改一行 → 分享 viewer。
2. 应用详情「发布」→ 无痕窗口打开 `/p/{token}` → 改 HTML 不重新发布（公开页不变）→ 重新发布（更新）。
3. 对话里让 Agent 创建应用并 `app_open` → 侧栏打开 → 页面 `composeMessage` 预填输入框。
4. `octopApp.launchGame` → 侧栏游戏 → 结果回写 → 应用刷新。
5. 工作台拖拽/缩放/锁定/撤销/全屏/自动排列，两个标签页同时改布局验证 CAS 冲突提示。
6. 上传含身份证列的文件 → 默认不导入 → 标记 `pii` 后公开链接读取显示掩码。
7. IM 私聊 `/bind` 绑定 owner → `app_share_link` 返回一次性链接 → 兑换后只能访问该 app。
8. 配一个数据源 + `sync_source` 调度 → 观察 `last_status` / `next_run_at`；再发一条入站 Webhook 测试事件。
9. 成员页：owner 添加平台成员 / guest、创建邀请与加入码、逐表开启 `member_visible`；agent 宿主只读审批。
10. 非成员打开 `/apps/{id}` → 403「申请加入 / 使用加入码」；受邀 guest 走 `/join` → `/my` → 应用，只看到可见表和自己的行。
11. IM 边界：群聊 / 未绑定私聊只应拿到链接 / 加入码，app 数据工具应被拒绝；guest 在 `/my` 生成 `/link` 码，私聊发送后 `app_share_link` 返回个人入口。

---

## 7. 已知缺口与风险

### 7.1 功能缺口

| 缺口 | 影响 | 备注 |
|---|---|---|
| IM per-sender actor（P2b） | IM 会话 / 记忆仍归 agent owner；PR-11 只收口工具与链接，不切换 thread / memory actor | 需要 per-sender thread / memory 隔离，单独设计 |
| 公开填报 | 公开链接只读；外部写入必须走签名入站 Webhook | 匿名公开填报属 P3；受邀 guest 可登录写自己的行 |
| R2 外部服务 `light_app_services` | 页面可通过宿主调用已声明的只读连接器工具 | 已支持 CRUD/调用/TTL 缓存/图片资产/公开缓存调用/Agent 工具；promote 待做 |
| R2 应用级调度 `light_app_schedules` | 统一管理数据源同步与公开快照刷新 | `sync_source` / `snapshot_publication` 已落地；`run_agent` 等动作待做 |
| R2 公开看板 | Board 不能分享 | 仅个人看板 |
| R3 Bundle v2 | 不能跨主机迁移应用 | 只有同实例 zip 备份/恢复 |
| R3 认证链接 / 渠道身份 | D4-lite 已落地：owner 绑定后发 app-scoped 免密链接；未绑定/群聊只读 | ✅ |
| 云端模板市场 | 只能系统/专家模板 | 前端占位 |
| 按应用选择模型 | 助手/生成固定用 owning agent 默认模型 | `model_access.py` |

### 7.2 架构风险

| 风险 | 现状 | 缓解/后续 |
|---|---|---|
| 多实例限流不全局 | 限流/锁是进程内 | 需要全局时用 DB 租约或网关限流 |
| 行数限额 check-then-insert | 多实例并发可能略超 2 万 | 当前单实例为主；多实例需条件 UPDATE/计数表 |
| 沙箱资源隔离 | 与 Dashboard 同 renderer process | 独立内容域或服务端浏览器 |
| 浏览器差异 | 已用 Chromium 回归 | Firefox/WebKit 建议补跑 |
| 公开 token 明文副本 | `secrets` 表保存一份用于复用 | 依赖 DB 与 secrets 的访问控制；轮换会作废旧链接 |
| 远程工作区写放大 | `app.html`/assets 仍走对象存储 | 行数据已入库；HTML 写入频率低 |
| 旧应用静态 frame | 渲染时拒绝并显示错误页 | 需要用户让 Agent 重写 |
| 团队 host 无 app 工具 | 团队只能通过交付物引用应用 | 设计如此，避免 host 直接操作应用 |
| IM 会话归 owner | IM 入站仍把外部 sender 归到 agent owner 的 thread / memory；PR-11 已收口 app 工具与个人链接 | P2b 再做 per-sender thread / memory；群聊消息仍共享 owner 的会话上下文 |

### 7.3 文档漂移

- 已修正：`docs/light-apps-architecture.md` 的“未实施”状态。
- **待同步**：`docs/architecture-overview.md` 仍是 2026-11-08 旧数据（35 个工具、50 张表、v29）；本文不以其为准。
- 已删除：`light-apps-multi-table-design.md`、`light-apps-assistant-and-import-design.md`（内容已并入本文 §2.2 / §2.6 / §2.7），以及过时的演示稿 `miniapp-platform-roadmap.html` / `.pptx`。
- 所有保留的 MiniApp 设计稿已加统一的“当前实现状态见 `miniapp-overview.md`”指针；设计稿只保留动机、取舍和历史记录。
- `docs/miniapp-platform-roadmap.md` 的 R1 状态准确；R2 的外部服务/事件/调度、R3 的 IM 公开链接与 owner 身份绑定（D4-lite）已落地；公开看板与迁移包 v2 仍未做。
- `docs/miniapp-identity-access-plan.md` 是 v3 设计；P0/P1/P2a 已实现（v48，PR-01~12）。
- 本文作为当前状态的单一入口。

---

## 8. 代码索引

| 关注点 | 位置 |
|---|---|
| 应用服务 | `src/octop/infra/apps/service.py` |
| Schema / 行 / HTML 校验 | `src/octop/infra/apps/models.py` |
| Agent 工具 | `src/octop/infra/apps/tools.py` |
| 能力清单 | `src/octop/infra/apps/capabilities.py` |
| 沙箱 / CSP / wrapper | `src/octop/infra/apps/sandbox.py` |
| 限流 | `src/octop/infra/apps/limits.py` |
| 只读助手 | `src/octop/infra/apps/assistant.py` |
| 一次性生成 | `src/octop/infra/apps/generation.py` |
| 图表聚合 | `src/octop/infra/apps/charts.py` |
| PII | `src/octop/infra/apps/pii.py` |
| 表格分析 | `src/octop/infra/apps/inspect.py` |
| 导入解析 | `src/octop/infra/apps/importer.py` |
| 快速模式渲染 | `src/octop/infra/apps/viewgen.py` |
| 模板 | `src/octop/infra/apps/templates.py` |
| 封面 | `src/octop/infra/apps/preview.py` |
| 数据源 / 调度 | `src/octop/infra/apps/sources.py`、`scheduler.py` |
| 外部服务 / 数据集 | `src/octop/infra/apps/dataset.py` |
| 出站 Webhook | `src/octop/infra/apps/webhooks.py` |
| app-owned 入站入口 | `src/octop/infra/apps/ingress.py` |
| 工具风险 / HITL | `src/octop/infra/apps/tool_policy.py`、`hitl_preflight.py`、`integration_ops.py` |
| 看板 | `src/octop/infra/apps/boards.py`、`infra/db/repos/boards.py` |
| 游戏 | `src/octop/infra/apps/game_sessions.py` |
| 对话内应用 | `src/octop/infra/apps/session.py` |
| 系统提示 | `src/octop/infra/apps/prompt.py` |
| 应用 HTTP | `src/octop/api/routers/apps.py` |
| 公开 HTTP / 宿主页 | `src/octop/api/routers/public_apps.py` |
| 看板 HTTP | `src/octop/api/routers/boards.py` |
| 游戏 HTTP | `src/octop/api/routers/game_sessions.py` |
| 访问申请 / 认证链接 / IM 绑定 | `src/octop/api/routers/app_access.py`、`app_access_link.py`、`channel_bindings.py` |
| 应用仓储 | `src/octop/infra/db/repos/apps.py` |
| 迁移 | `src/octop/infra/db/migrations/025–046_*`、`migrate.py` |
| 专家手册 | `src/octop/infra/agents/experts/library/light-apps/AGENTS.md` |
| 前端工作台 | `dashboard/src/pages/Apps/` |
| 前端宿主 | `dashboard/src/components/MiniAppFrame/` |
| 前端对话 | `dashboard/src/pages/Chat/components/ChatDockSessionApps.tsx`、`utils/sessionApps.ts` |
| 前端 API | `dashboard/src/api/modules/apps.ts`、`boards.ts` |
| 测试 | `tests/unit/apps/`、`tests/integration/test_apps_api.py`、`test_boards_api.py`、`test_miniapp_sandbox.py` |
