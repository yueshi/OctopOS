# Magic HTML 微应用与 MagicBase 对 MiniApp 的参考

> 日期：2026-11-17
> 调研基线：`dtyq/magic` master `0f1432c3`（2026-10-24 推送，约 5.1k stars）
> 范围：Magic 的 HTML 微应用运行时、MagicBase 数据面，与 Octop MiniApp 的架构 / 功能对照。
> 关系：补充 [MiniApp 功能与架构现状](./miniapp-overview.md)、[MiniApp 产品定位与路线图](./miniapp-product-plan.md)、[业务 AgentOS 实施路线](./agent-os-roadmap.md)，不替代它们。
> 说明：本文只提炼设计，不复制 Magic 代码或文案。Magic 使用修改版 Apache 2.0（禁止未经书面许可的多租户 SaaS、要求保留品牌），与 Octop 的 MIT 不兼容。

---

## 0. 结论

**两者是同一类形态——宿主代理的零后端轻应用——但强项不同。**

- Magic 强在**契约与治理**：`app.json` 声明式权限、运行时按次授权、匿名访问与表单草稿、可信 `@micro_app` 身份、计划审批、分享治理、MagicBase 的表/行/列权限与关联查询。
- Octop 强在**沙箱与数据层**：opaque-origin 双层 iframe + 严格 CSP、`light_app_tables` 的多表/CAS/PII/ACL/CSV、桌面宿主与窗口体系。

因此借鉴原则是：**拿 Magic 的声明式契约、公开面、Agent 协作流程；保留 Octop 的沙箱、数据权威与桌面宿主**。不要整体换架构。

最值得借鉴的五件事：

1. **Manifest 声明式权限**：应用自己声明要什么、为什么，运行时按次授权，服务端仍做最终校验。
2. **匿名表单与草稿**：公开链接从只读扩展为可提交，使用 guest 身份、限流、PII 脱敏和登录回跳草稿。
3. **应用发起 Agent 任务**：页面不直接持有 Agent 身份，宿主注入可信 `@miniapp` mention，复杂流程交给 companion skill。
4. **分享治理**：公开/团队/密码、过期、访问统计、安装安全提示。
5. **MagicBase 式关系与权限模型**：reference 列、关联展开、动态 scope、anonymous subject，配合 prepared operation 做数据写入确认。

不建议照搬：同源 iframe shell、PHP/MongoDB 多服务与分片、完整 MagicBase、工作流/规则引擎。

---

## 1. 定位与架构对照

### 1.1 两边分别是什么

Magic 的「HTML 微应用」有三层形态：

| 形态 | 存储 | 用途 |
|---|---|---|
| **发布微应用**（`micro-app.agent`） | `.magic/skills` + MagicBase | 可发布、可分享、可匿名访问的正式微应用 |
| **普通项目 HTML 应用**（`html-app-architect`） | 工作区文件（一键一记录） | 项目内的轻量工具、表单、看板 |
| **Magic Project 产物**（`magic.project.js`） | 文件夹 + manifest | slide / audio / html-app 等「超级产物」面板 |

Octop 的 MiniApp 是单一形态但数据层更完整：一份 HTML（≤2MB）+ 多表数据（12 表/应用、50 列、2 万行、5MB CSV）+ 能力位 + ACL + 桌面/公开页宿主。

### 1.2 分层对照

| 层 | Magic | Octop 现状 | 判断 |
|---|---|---|---|
| **宿主 / 沙箱** | 同源 `/husky.html` 或跨域 `MAGIC_HTML_SANDBOX_URL`；postMessage + Magic API prelude | opaque-origin 双层 iframe + 严格 CSP + `octopApp` bridge | **Octop 更强，不换** |
| **Manifest** | `app.json` 是 source-of-truth，含 `anonymous`、`permissions.scopes`、`files`、`watch`；`magic.project.js` 只做展示桥 | DB 管 capabilities + 备份 manifest；没有声明式 scopes/reason | **借声明层，保留 DB 权威** |
| **页面 API** | `fs / llm / db / agent / project / user / i18n` | `rows / schema / tables / generate / composeMessage / service / events / asset` | **互补；补 fs、llm、Agent 任务** |
| **数据面** | MagicBase：表/列/行/relation/权限；MySQL 元数据 + MongoDB 行 | `light_app_tables` / `light_app_rows`：多表、CAS、PII、ACL、CSV；SQLite/PG | **Octop 更强；借 relation 与 scope 模型** |
| **权限** | 声明 scopes + 运行时授权；MagicBase 动态 scope + subject | 11 个能力位 + owner/ACL；没有 per-call 运行时授权 | **借 scopes/consent** |
| **AI 协作** | `llm.chat/stream` + `createTopicAndSend` + companion skill + 可信 mention | `generate` + `composeMessage`；没有通用 Agent 任务 | **补 Agent 任务** |
| **匿名 / 公开** | `anonymous` + 表单草稿 + guest/team/anonymous share actor | token 化只读公开快照 | **补匿名写/公开表单** |
| **分享** | shareType/range/password/expireDays/access log/install safety | token 只读；无密码/过期/统计 | **补分享治理** |
| **生成流程** | `micro_app_plan` 计划审批 + `micro-app-architect` + `MICRO-APP.md` + `skill-creator` | Agent 生成 + 模板 + 访问申请 | **补计划闸门、companion skill、项目记忆** |
| **产物** | `magic.project.js`（slide/audio/html-app）+ Canvas | 工作区文件 + 桌面窗口 | **可选借 artifact 约定** |
| **审计** | schema migration log + share access log + model audit | 管理动作 audit；行级历史在补 | **Octop 补行级历史，Magic 没有** |

### 1.3 共同点与关键差异

共同点：

- 单页 HTML，页面不持有 JWT、数据库凭据或 LLM key。
- 所有副作用经宿主代理，页面只发消息 / 调 API。
- 能力或 scope 先声明后使用，高风险动作要用户确认。
- 可发布、可分享；公开页与登录页共用同一渲染层。

关键差异：

- Magic 提供**通用宿主 API**（文件、LLM、Agent 任务），Octop 提供**更多数据操作**（多表、CAS、PII、事件）。
- Magic 的 manifest 是应用自己声明的契约；Octop 的能力位是 DB 管理与 owner 授权。
- Magic 的公开面走「分享 + 匿名」；Octop 目前只有只读快照。
- Magic 的沙箱是宿主 shell（同源默认）；Octop 是不透明源双层 iframe，安全边界更硬。

---

## 2. Magic 的微应用架构拆解

### 2.1 Manifest 与文件布局

正式微应用的根目录就是应用根，必须直接包含：

| 文件 | 作用 |
|---|---|
| `app.json` | 宿主读取的 source-of-truth：`version`、`type: "micro-app"`、`name`、`entry`、`anonymous`、`files`、`watch`、`permissions.scopes` + `reason` |
| `magic.project.js` | 展示兼容桥，只镜像 `version`、`type`、`name`、`entry`、`icon`；**不放权限、业务状态、schema** |
| `MICRO-APP.md` | 项目记忆，页面不读；由 `update_html_app_memory` 维护 |
| `index.html` | 单页入口；多页面也必须直接放根目录 |
| `icon.svg` | 本地图标；不用远程 URL、截图或 data URL |

约束：不允许 `src/`、`app/`、`web/` 包装目录；不允许 `../`；业务状态放 `data/`，不放 manifest。见 `backend/super-magic/agents/skills/micro-app-architect/SKILL.md`。

### 2.2 运行时与宿主 API

页面加载链路：宿主先加载 shell（同源 `/husky.html` 或跨域 shell）→ 通过 `setContent` 写入用户 HTML → prelude 在 `<head>` 最前注入 `window.Magic` → 页面脚本执行。关键工程约束：

- prelude 必须在业务脚本之前，否则用户同步脚本调用 `window.Magic.*` 会竞态。
- LLM token 只存在宿主内存，不通过 postMessage 返回 iframe。
- 文件路径相对应用根；项目根写入要 `fs.project.write` + 宿主路径确认。
- 相对资源、fetch/XHR、动态 DOM、嵌套 iframe 都有解析与循环检测；保存时要清理注入脚本、临时 URL、`srcdoc`。

页面 API 概览：

| 命名空间 | 方法 | 用途 |
|---|---|---|
| `window.Magic.fs` | `readFile` / `writeFile` / `listFiles` / `listDir` / `watchFile` / `watchDir` | 工作区文件读写与监听 |
| `window.Magic.llm` | `getModels` / `chat` / `stream` | 单次推理；需要 `llm.use` |
| `window.Magic.db` | 表查询 / 行 CRUD / `getProjectAdminAccess` | MagicBase 数据 |
| `window.Magic.project` | `createTopicAndSend` / `sendMessage` / `uploadFiles` / `downloadFiles` | Agent 任务与文件传输 |
| `window.Magic.user` | `getInfo({ scopes, reason })` | 用户资料，敏感字段要声明 + 确认 |
| `window.Magic.agent` | `getAgents` | Agent 列表 |
| `window.Magic.i18n` | `subscribe` | 语言变化 |

AI 路由规则很值得借鉴：**单次推理、输入完整 → `llm.chat/stream`；需要工具、多步、文件处理、外部系统 → `createTopicAndSend` + companion skill；不要在页面里维护伪 Agent 循环**。见 `agents/skills/micro-app-architect/SKILL.md` 的 AI Execution Routing。

### 2.3 权限与匿名

`app.json.permissions.scopes` 的常用 scope：

| Scope | 对应的页面能力 |
|---|---|
| `llm.use` | `llm.chat` / `llm.stream` |
| `fs.project.read` | 项目根读取 |
| `fs.project.write` | 项目根写入 / 删除 / 移动 / 重命名 |
| `project.files.upload` | `uploadFiles` |
| `project.files.download` | `downloadFiles` |
| `project.message.write` | `createTopicAndSend` / `sendMessage` |
| `user.profile.name` / `identity` / `organization` | `user.getInfo` 的敏感字段 |

运行时授权只对当前标签页有效、有期限、可撤销；未声明 scope 的高风险调用会被拒绝。历史应用没有 manifest 时按 legacy 处理：审批时长更短，并提示「没有权限声明」。

匿名语义：

- `app.json.anonymous: true` 时，浏览、填表、本地计算不需要登录或 runtime project。
- 初始化、读上下文、恢复草稿时**不创建 runtime project、不发 Agent 消息**；项目创建与登录确认由宿主负责。
- 表单草稿用 `sessionStorage`，由应用自己实现；登录回跳后恢复，但不自动提交；密码、凭据、文件内容不落草稿。

分享身份模型：

- `ShareUserType`：`guest`（其他组织登录用户）、`team_member`（同组织登录用户）、`anonymous`（未登录）。
- 分享 token 只做访问证明，不把分享创建者当成当前用户；管理页要用 `getProjectAdminAccess()` 判断真实登录用户。
- 数据写入可以使用发布授权，但表结构 / 权限管理仍要求协作角色。

### 2.4 生成流程：计划审批 + companion skill + 项目记忆

**计划审批**（`micro_app_plan`）在写 HTML / schema 前展示：

- `requirements`、`implementation_steps`、`files`、`data_model`、`acceptance_criteria`、`assumptions`。
- 状态：`approved` / `revision_requested` / `cancelled` / `timeout`。
- 权限相关必须标注 `permission_feasibility`：`enforceable_by_magicbase`、`ui_only_not_secure`、`requires_backend`。
- 未批准前不写业务文件、不改 schema。

**Companion skill**：复杂业务用 `skill-creator` 生成 `.magic/skills/<name>/SKILL.md`；运行时第一次 `read_skills` 必须带 mention 的 `project_id`，不能回退到当前工作区同名 skill。

**项目记忆**：`MICRO-APP.md` 通过 `update_html_app_memory` 更新，页面不读；用于记录已完成状态和后续开发上下文。

### 2.5 分享与发布治理

Magic 的发布参数包括：

- `shareType`、`shareRange`、`targetIds`
- `password`、`expireDays`
- `coverFileKey`、`defaultOpenFileId`
- `extra.pure_mode`

分享页有安全提示、安装对话框（PWA）、权限管理面板；访问有统计与复制日志。见 `backend/magic-service/app/Application/SuperMagic/Project/DTO/Request/PublishMicroAppRequestDTO.php:12`、`ShareUserType.php`。

### 2.6 MagicBase 数据面

MagicBase 是给微应用用的「托管多表数据库 + 表/行/列权限 + 关联查询」。

**模型**

- 表：key、名称、描述、状态、owner department、动态权限。
- 列：类型 `text / number / datetime / boolean / json`；必填、默认值、options。
- 行：动态 JSON，按 `column_key` 存值；系统字段 `created_by`、`organization_code`、`created_at`、`updated_at` 由平台维护。
- 关联：`belongs_to / has_one / has_many`；查询时可用 `alias:source_column(fields)` 展开。

**查询**

- filter DSL：`eq / in / contains / gt / gte / lt / lte`，最多 10 个条件；`in` 最多 100 个值；`contains` 2–100 字符。
- sort、page/pageSize、`select` 投影、`includeTotal`；不写 SQL。
- 没有看到专门的聚合 / 导出端点；通常由页面或 Agent 取数后计算。

**权限**

- 动态 scope：`public / private_user / private_department / private_org / disabled`。
- 显式 subject：组织 / 部门 / 用户 / 匿名；级别 `read / insert / manage`。
- 表 / 行 / 列三级权限；项目管理员、表管理员。
- 分享运行时 actor 与真实登录用户分离。

**存储**

- 元数据在 MySQL；行数据在 MongoDB。
- 按 project 路由到不同 database / collection，支持分片（`MagicBaseProjectStorageRouteEntity`）。
- schema 变更写 migration log（before/after、operator、request_id）；**没有行级变更历史**。

**Agent 工具**

`query_magicbase_tables`、`query_magicbase_rows`、`get/batch_get`、`create/update/delete`、批量增删、建表/改列、权限管理；写操作要求 preview → `ask_user` → `confirmed_by_user`。注意：`confirmed_by_user` 只是标志位，Magic 自己也注明它不是可独立验证的 token。见 `backend/super-magic/app/tools/magicbase_tools.py:470`、`agents/skills/use-magicbase/SKILL.md`。

**页面 API**

`window.Magic.db` 只暴露 `getTables / getTable / queryRows / createRow / updateRow / deleteRow / getProjectAdminAccess`，不暴露建表、改列、权限管理。见 `frontend/magic-web/.../iframe-api/magic-api/MagicDatabaseApi.ts`。

### 2.7 产物项目（可选参考）

`magic.project.js` 让一个文件夹成为「Magic Project」：前端显示项目图标，点击打开专属面板；类型有 `slide`、`audio`、`html-app`。整个文件夹被称为「超级产物」。它和微应用的区别是：微应用是 manifest + 页面 + 数据；产物项目是文件夹 + 页面 + 展示元数据。见 `agents/prompts/magic_project_mechanism.prompt`。

---

## 3. 对 Octop 的借鉴建议

### 3.1 Manifest 声明式权限

**Magic 做法**：`app.json` 由 Agent 在 workspace 生成，声明 `version`、`type`、`name`、`entry`、`anonymous`、`permissions.scopes`、`reason`、`files`、`watch`；高风险 API 先声明后授权。

**Octop 现状**：`capabilities.py:32` 已有 11 个能力位、legacy/new 安全默认集、owner 审批；但 manifest 是备份快照，没有「应用声明自己要什么、为什么」的可审阅层。

**建议**：

1. 在应用工作区增加 `app.json`（或 `miniapp.json`）：`version`、`type`、`name`、`entry`、`anonymous`、`permissions.scopes`、`reason`、`files`、`watch`。
2. **DB 仍是权威**：manifest 只作为生成时的声明 + 安装/分享时的展示；服务端校验后写入 capabilities，页面传 manifest 不生效。
3. `scopes` 与现有能力位做映射；新增能力必须走 `CAPABILITIES_VERSION`。
4. 分享/安装页展示「这个应用要什么、为什么」，owner 可撤销。

**优先级**：P1，对应 M3.1 / M3.3。

### 3.2 宿主 API 与可信身份

**Magic 做法**：prelude 注入 `window.Magic`；宿主执行副作用；token 不出宿主；`user.getInfo` 按 scope 授权；`createTopicAndSend` 由宿主注入可信 `@micro_app` mention，应用不能伪造 `appId/projectId`。

**Octop 现状**：`octopApp` 有数据 CRUD、`generate`、`composeMessage`、`service`、`events`、`asset`；没有通用 Agent 会话 API，也没有 per-call 授权。

**建议**：

1. 增加 `octopApp.requestScopes(scopes, reason)`：宿主弹授权框，按标签页/时长生效；服务端仍用 `require_capability` 二次校验。
2. 增加 `octopApp.chat.sendTask()`：新能力 `chat.task`；宿主创建/复用 topic，注入可信 `@miniapp <app_id>` mention；服务端从 mention 解析 app id，**不接受页面传入的 app_id/project_id**。
3. 结果回传：任务完成后通过 `onDataChange` / `app_event` / 通知中心回显；页面不轮询 Agent 内部状态。
4. 保持 token 不出宿主：页面只拿短期结果句柄。

**优先级**：P1，依赖 M2 的隔离与审计。

### 3.3 匿名访问与公开表单

**Magic 做法**：`app.json.anonymous` 显式声明；匿名应用可浏览、填表、本地计算；初始化不创建 runtime project、不发 Agent 消息；登录确认由宿主负责；草稿用 `sessionStorage`，登录回跳恢复但不自动提交。

**Octop 现状**：公开链接是 token 化只读快照，不能写、不能发事件。

**建议**：

1. 公开发布增加 `allow_anonymous_write`（默认关闭），仅对指定表/行开放 insert（可选 update）。
2. 匿名身份用 guest token/cookie，记录 `created_by_type=guest`；限流、CSRF、PII 脱敏、审计。
3. 表单草稿按 Magic 的规则实现：稳定 app/form key + schema version、登录回跳恢复、取消保留、成功/重置才清空、不自动提交。
4. 公开页明确「这是匿名提交」；管理端可查来源、可关闭写入。

**优先级**：P1，产品价值高（报名、问卷、线索收集）。

### 3.4 应用发起 Agent 任务 + companion skill + 项目记忆

**Magic 做法**：页面 `createTopicAndSend` / `sendMessage`；宿主注入 `@micro_app` mention；companion skill 放 `.magic/skills/<name>/SKILL.md`，按来源 project id 加载；`MICRO-APP.md` 记录项目记忆。

**Octop 现状**：`composeMessage` 只预填输入框；`generate` 是一次性生成；助手是只读 SSE。没有通用 Agent 任务，也没有 companion skill 约定。

**建议**：

1. 增加 `chat.task` 能力与 `octopApp.chat.sendTask()`；宿主负责 topic、登录确认、结果回显。
2. 引入 companion skill：`.octop/skills/<name>/SKILL.md`，随应用源项目存放；Agent 通过来源 project id 加载，禁止当前工作区回退。
3. 引入 `MINIAPP.md` 项目记忆（由工具更新，不让页面读写）；记录数据模型、已完成功能、权限边界。
4. 在生成提示里明确 AI 路由，避免页面里出现「循环调 generate 模拟 Agent」。

**优先级**：P1，是 MiniApp 从「表单展示」升级为「AI 工作流前端」的关键。

### 3.5 MagicBase 式数据模型与查询

**Magic 做法**：relation、filter DSL、select 投影、动态 scope、anonymous subject、Agent 工具 preview→confirm。

**Octop 现状**：`light_app_tables` 更完整（多表、行级 CAS、PII、行级 ACL、CSV、公开快照）；reference 列（belongs_to + `expand_refs` label）、aggregate、行级历史已落地（2026-11-18）；`has_one / has_many` 展开与 P0-4 查询下推还没做。

**建议**：

1. **继续补 relation 展开**：`has_one / has_many` 反向读取；`belongs_to` 已落地（不做级联、不做多对多，与 P0-2 一致）。
2. **补 aggregate 端点**：`group_by / agg`，复用 `charts.py`；页面和助手都走它，禁止把全表拉到前端算。
3. **查询 DSL 收口**：限制条件数、`in` 值数、`contains` 长度，强制字段级读权限；过滤/排序下推 SQL（P0-4）。
4. **加 anonymous/guest subject**：为公开表单提供可控写入身份；share actor 不能被当成真实用户。
5. **Agent 数据工具走 prepared operation**：绑定准备快照与 fingerprint，避免「确认后被改参数」。
6. **不抄** MongoDB/MySQL 分离和分片：单节点 Octop 用现有 SQLite/PG 即可；只借「元数据与行数据分离」的思路。

**优先级**：P0（对应 P0-2 / P0-4）；anonymous subject 可随匿名表单一起做。

### 3.6 计划审批与生成工作流

**Magic 做法**：`micro_app_plan` 在写 HTML/schema 前展示计划；状态 approved / revision_requested / cancelled / timeout；权限相关标注 `permission_feasibility`。

**Octop 现状**：Agent 可以直接生成/修改应用；有访问申请和 ACL，但没有「写前计划审批」的统一闸门。

**建议**：

1. 增加 `miniapp.plan` 工具与审批 UI，至少覆盖：新建应用、改 schema、改 HTML 结构、改权限。
2. 计划里强制写「权限可行性」：哪些规则页面能保证、哪些只是 UI 遮罩、哪些必须后端。
3. 计划落库，作为审计和回滚依据；审批超时保持 pending，不自动通过。
4. 与 prepared operation 结合：批准的是「这份计划 + 这份文件清单」。

**优先级**：P1，直接降低 Agent 误改应用的风险。

### 3.7 分享治理

**Magic 做法**：shareType/range/password/expireDays/cover/defaultOpen/extra pure mode；guest/team/anonymous；访问统计、复制日志；安装对话框与安全提示。

**Octop 现状**：公开链接只有 token + 只读快照；没有密码、过期、访问统计、安装流程。

**建议**：

1. 增加分享类型：公开 / 团队 / 密码保护；`expire_days`、访问日志/统计、封面、默认打开文件。
2. 公开页加安全提示与「添加到桌面」（可选 PWA）。
3. 分享 token 与用户身份严格分离；管理接口必须用真实登录用户判断管理员。
4. 发布历史（Octop 的 P1-9）单独做，Magic 的 share 模型不提供版本历史，不要混淆。

**优先级**：P1/P2，取决于是否要对外分发。

### 3.8 产物项目与 iframe 工程（可选）

**Magic 做法**：`magic.project.js` 让文件夹成为项目；iframe 规范包含 prelude 注入顺序、同源/跨域消息目标策略、相对资源解析、嵌套 iframe、循环检测、保存清理、token 不出宿主。

**Octop 现状**：有桌面窗口和 MiniAppFrame，但工作区文件夹没有「项目」语义；CSP 严格（`connect-src 'none'`，远程资源全禁），表达力比 Magic 弱，但安全性更强。

**建议**：

1. 增加 `octop.project.json` 约定：`type`（deck/dashboard/html-app/report）、`name`、`entry`、`version`、`icon`；文件树识别为项目，复用 MiniAppFrame 或专用面板打开。
2. manifest 只存展示与入口，不存业务状态/schema。
3. 如果要支持更丰富的页面（CDN、外部图表库），不要直接放宽 CSP；增加宿主代理的 `asset`/`service` 能力，或单独的跨域 shell，保持 token 与数据边界。
4. 如果做 HTML 编辑/预览，参考 Magic 的 prelude 注入顺序、资源替换与保存清理清单。

**优先级**：P2，属于展示层增强。

---

## 4. 不建议借鉴

| 部分 | 原因 |
|---|---|
| 同源 iframe shell | Magic 自己承认同源不是硬隔离；Octop 的 opaque-origin 双层 iframe + CSP 更强 |
| PHP/MongoDB 多服务与分片 | 与 Octop 单进程、单节点、自托管前提冲突 |
| 完整 MagicBase | Octop 的 `light_app_tables` 在多表、CAS、PII、ACL 上更完整；只借 relation/scope 模型 |
| `confirmed_by_user` 作为唯一确认凭据 | Magic 自己注明它不是可独立验证的 token；Octop 应使用 prepared operation 绑定 fingerprint |
| 工作流/规则引擎 | Octop 明确不是业务流程引擎，编排归 cron/events/Teams |
| 完整 PWA 安装/Canvas | 取决于产品是否需要；先做轻量分享与 artifact 约定 |
| 直接复制代码/文案 | Magic 是修改版 Apache 2.0（多租户 SaaS 限制 + 品牌保留），Octop 是 MIT；只能借鉴设计后重写 |

---

## 5. 落地顺序

| 阶段 | 借鉴项 | 产出 |
|---|---|---|
| **M1 治理** | manifest scopes + 运行时 consent；prepared operation 绑定审批；审计记录「批准了哪份操作」 | 权限声明、per-call 授权、可举证审批 |
| **M2 执行** | 沙箱/配额（app→agent 任务的前置）；app 任务的超时/取消/预算 | 安全执行 Agent 任务 |
| **M3.1 / M3.3** | `app.json` 声明、能力版本化、服务端 `require_capability`、分享治理 | 可安装/可分享/可审计的应用 |
| **M3.2 数据** | reference 列 + aggregate + 查询下推 + anonymous subject + Agent 数据工具 prepared operation | 台账/报表/公开表单成立 |
| **M3 产品** | 匿名表单、`chat.task`、companion skill、`MINIAPP.md`、`miniapp.plan` 闸门 | AI 工作流前端 |
| **P2 展示** | `octop.project.json` artifact、iframe 工程增强 | 交付物与富页面 |

**如果只能做三件**：先做 **manifest 声明式权限**、**匿名表单写入**、**应用发起 Agent 任务**。这三件把 MiniApp 从「带数据的静态页」变成「可对外、可协作、可调用 Agent 的轻应用」，而且都能复用 Octop 现有沙箱和数据层。

---

## 6. 证据索引

以下 Magic 路径均相对 `dtyq/magic` master `0f1432c3`。

| 主题 | Magic 路径 | Octop 对照 |
|---|---|---|
| 微应用契约 | `backend/super-magic/agents/skills/micro-app-architect/SKILL.md` | `src/octop/infra/apps/capabilities.py:32` |
| 页面 API / 权限 | `backend/super-magic/agents/skills/html-api-sdk/SKILL.md` §6.5 | `dashboard/src/components/MiniAppFrame/sdk.ts` |
| iframe 规范 | `frontend/magic-web/src/pages/superMagic/components/Detail/contents/HTML/docs/html-iframe-runtime-flow-spec.md` | `dashboard/src/components/MiniAppFrame/wrapper.ts`、`appCsp.ts` |
| MagicBase 模型 | `backend/magic-service/app/Domain/MagicBase/Entity/*.php` | `src/octop/infra/apps/models.py` |
| MagicBase 权限 | `backend/magic-service/app/Domain/MagicBase/Entity/ValueObject/MagicBaseConst.php:18-66`、`Application/MagicBase/Support/MagicBaseAccessControl.php:35` | `src/octop/infra/apps/service.py`（ACL/capabilities） |
| MagicBase 查询 | `backend/magic-service/app/Domain/MagicBase/Service/MagicBaseRowFilterDomainService.php`、`Application/MagicBase/Support/SelectParser.php` | `src/octop/infra/apps/service.py`（filter/sort） |
| Agent 数据工具 | `backend/super-magic/app/tools/magicbase_tools.py:470`、`agents/skills/use-magicbase/SKILL.md` | `src/octop/infra/apps/assistant.py` |
| 计划审批 | `backend/super-magic/app/tools/micro_app_plan.py:110` | Agent 生成流程 |
| 分享模型 | `backend/magic-service/app/Application/SuperMagic/Project/DTO/Request/PublishMicroAppRequestDTO.php:12`、`Domain/SuperMagic/Common/Share/Constant/ShareUserType.php` | `src/octop/infra/apps/service.py:4204` |
| 匿名 / 草稿 | `micro-app-architect` Anonymous Access 章节 | 公开链接只读 |
| 产物约定 | `backend/super-magic/agents/prompts/magic_project_mechanism.prompt` | `dashboard/src/pages/Apps/`、MiniAppFrame |

---

## 7. 待定问题

1. **匿名写入的范围**：只允许 insert，还是允许 update/delete？哪些表开放？
2. **外部资源策略**：是否允许 CDN / 外部图表库？如果允许，走跨域 shell 还是宿主代理？
3. **安装形态**：是否做 PWA「添加到桌面」，还是只做浏览器内分享页？
4. **Manifest 的生成方**：由 Agent 在 workspace 生成，还是由服务端生成后下发？两者如何保持单一权威？
5. **数据模型优先级**：reference/aggregate 与行级历史谁先做？两者都影响 V2/V3 验收。
