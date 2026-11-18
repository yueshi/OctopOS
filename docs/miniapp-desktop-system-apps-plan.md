# Agent 桌面系统应用：用户配置功能调研与落地计划

> 快照：2026-11-17，分支 `feature/miniapp-capabilities`，schema **v39**。
>
> 本文是**方案与计划**，不是现状说明；已落地的事实以代码与 [MiniApp 功能与架构现状](./miniapp-overview.md) 为准。
>
> 术语：本文把「以 MiniApp 形态出现在 Agent 桌面上的平台自有配置/经营界面」称为 **系统应用（System App）**。它与用户/Agent 创建的 MiniApp 共用窗口和沙箱宿主，但不进入 `light_apps` 数据模型。

---

## 0. 结论摘要

1. **不要把用户配置功能做成由 Agent 生成、写入 `light_apps` 的普通 MiniApp。** 配置数据横跨 `agents`、`channels`、`connector_instances`、`skills`、`usage_log`、`cron_jobs`、`memory` 等多个原生服务；写进 `light_app_rows` 会额外获得协作者、导出、备份、公开链接等扩散面，而且数据必然过期。凭证、OAuth、扫码登录更不应进入沙箱。
2. **推荐新增平台系统应用（System Apps）**：与 MiniApp 共用桌面窗口、沙箱 iframe、postMessage 桥和视觉语言；但不进入 `light_apps`，没有表/行、没有协作者、没有公开链接、Agent 不可编辑。数据由宿主桥按白名单**实时**代理现有 `/api`，权限仍由服务端判定。
3. **首批 4 个独立应用**：**用量与预算**、**连接（通道 + 连接器）**、**能力（技能 + 工具/插件）**、**经营看板（指标 / 预算 / 待办）**。四者各自拥有独立桌面图标和窗口，不做「一个聚合应用 + 标签页」；正好覆盖用户举例的 Token 消耗、通道、技能、连接器，且以只读/状态为主，**不需要后端改造**。
4. **二期优先做技能与任务**：**技能开关**（在「能力」应用内）、**任务应用 + 任务立即运行**。记忆统计可与任务同批，也可滑到 P3；通道启停、记忆候选审核留到 P3 再评估。
5. **重配置使用桌面原生管理面板**：系统应用只做摘要；凭证、OAuth、扫码登录、通道编辑、任务编辑等通过 `openManager` 在桌面 React 树内打开 `DesktopManagerDrawer`，复用可嵌入的功能面板，不加载 Console SPA，也不在沙箱 iframe 内复制表单。模型密钥、安全策略、备份、存储、TLS 等全局设置仍留在原生控制台。
6. 现有桌面空间是「按专家推导」的（`docs/miniapp-overview.md:302`），因此系统应用天然是**专家级视图**；账号级应用（全局用量、偏好、Bridge）建议放到三期的「我的桌面」空间，而不是塞进每个专家桌面。

---

## 1. 调研范围与判断标准

### 1.1 本文说的「用户配置功能」

面向终端用户、用于查看或修改 Agent / 账号运行方式的界面，而不是业务型 MiniApp。包括：

- 用量、预算、配额；
- 通道、连接器、技能、工具、插件；
- 记忆、知识库、定时任务、主动关怀；
- 专家运行参数（模型、上下文、限额）；
- 账号级设置（偏好、模型供应商、安全、备份、Bridge、外部事件等）。

### 1.2 判断标准

| 维度 | 问题 |
|---|---|
| 数据形态 | 只读聚合，还是需要多表关联/实时状态？ |
| 敏感度 | 是否包含凭证、API Key、完整配置、Prompt？ |
| 交互复杂度 | 列表/开关，还是多步表单、OAuth、扫码、文件上传？ |
| 权限边界 | 谁能看：用户本人、专家 owner、管理员、协作者？ |
| 新鲜度 | 打开时读一次即可，还是必须实时/轮询？ |
| 桌面价值 | 放在专家桌面是否能减少一次「进设置页找入口」？ |

### 1.3 适配度分级

| 级别 | 含义 | 典型形态 |
|---|---|---|
| ★★★★★ | 只读聚合/列表，权限清晰，桌面高频 | 用量趋势、技能列表、运行状态 |
| ★★★★ | 状态卡 + 简单开关 + 原生管理面板 | 通道状态、连接器状态、记忆统计 |
| ★★★ | 只读摘要 + 原生管理面板，编辑复杂 | 专家参数、知识库索引状态 |
| ★★ | 复杂表单/OAuth/凭证，或价值偏低 | 连接器新增、子代理、MBTI |
| ★ | 密钥/安全/全局基础设施，不应进沙箱 | 模型供应商、安全策略、备份、TLS |

---

## 2. 现有配置功能盘点

### 2.1 专家级

| 功能 | 现有入口 | 主要 API | 作用域 / 权限 | 敏感度 | 交互 | 桌面适配 |
|---|---|---|---|---|---|---|
| Token 用量 | `/token-usage`（`dashboard/src/pages/Control/TokenUsage/index.tsx`） | `GET /api/usage/summary?window&granularity&agent_id`（`src/octop/api/routers/usage.py:215`） | 用户本人；可按专家过滤；`token_usage` | 中（聚合） | 只读图表 | ★★★★★ |
| 数字员工经营（指标/预算/待办） | 无完整页面；`AgentCard` 只有 autonomy 开关（`dashboard/src/pages/Experts/components/AgentCard.tsx:259`） | `GET /api/agents/{id}/metrics`（`src/octop/api/routers/autonomy.py:294`）、`/inbox`（`:217`）、`/autonomy-policy` | 专家 owner | 中 | 只读 + 开关 | ★★★★★ |
| 专家运行状态 | `GET /api/agents/{id}/status`（`src/octop/api/routers/agents.py:580`） | 状态、通道、Cron、记忆维护 | owner | 低 | 只读 | ★★★★ |
| 专家参数（模型/温度/上下文/限额） | `/agent-config`（`dashboard/src/pages/Agent/Config/index.tsx`） | `GET/PATCH /api/agents/{id}` | owner | 中（含 Prompt） | 表单 | ★★★ 只读摘要 + 深链 |
| 通道 | `/personalization/channels`（`ChannelsPanel`） | `GET/POST/PATCH/DELETE /api/agents/{id}/channels`（`src/octop/api/routers/channels.py:156`） | owner；需 `channels` 权限 | **高**（配置含凭证） | 表单/扫码/OAuth | ★★★★ 状态卡 + 原生管理面板 |
| 技能 | `/personalization/skills`（`SkillsTabs`） | `GET /api/agents/{id}/skills`（`src/octop/api/routers/skills.py:486`）、enable/disable、Hub 安装 | owner 写；共享者可读；需 `skills` | 低/中 | 列表 + 开关 + Hub | ★★★★★ |
| 工具/插件 | `/personalization/tools`、`/personalization/plugins` | `GET/PUT/PATCH /api/agents/{id}/tool-settings`（`src/octop/api/routers/agent_tools.py:158`） | owner；需 `tools` | 中 | 开关/安装 | ★★★ 状态 + 开关 + 原生管理面板 |
| 连接器 | `/connectors`（`dashboard/src/pages/Agent/Connectors/index.tsx`） | `GET /api/connector-instances`（`src/octop/api/routers/connectors.py:555`）、OAuth 端点 | 用户账号级；`connectors` | **高**（凭证） | 多步表单/OAuth | ★★★ 状态卡 + 原生管理面板 |
| 知识库 | `/knowledge-bases`；专家通过 `knowledge_base_ids` 绑定（`src/octop/api/routers/agents.py:183`） | `GET /api/knowledge-bases`（`src/octop/api/routers/knowledge_bases.py:525`） | 用户可见范围 | 中 | 上传/索引 | ★★★★ 绑定状态卡 |
| 记忆 | `/personalization/memory`（`MemoryPanel`） | `GET /api/agents/{id}/memory/stats/*`（`src/octop/api/routers/memory.py:551`）、candidates | owner；`memory` | 中 | 列表 + 审核 | ★★★★ 统计（P2 可选）；审核（P3） |
| 定时任务 | `/tasks`（`dashboard/src/pages/Control/CronJobs`） | `GET /api/agents/{id}/cron`（`src/octop/api/routers/cron.py:118`）、run-now | owner / 可管理者；`tasks` | 中 | 列表/表单 | ★★★★ 列表 + run-now + 原生管理面板 |
| 主动关怀 | `MemoryPanel` 内 `ProactiveConfig` | `GET/PUT /api/agents/{id}/proactive-care` | owner | 中 | 表单 | ★★★ 状态 + 开关（P3） |
| 工作区文件 | 桌面已有 Files 窗口（`DesktopFilesWindow`） | workspace API | owner | 高 | 文件管理 | ✅ 已是原生窗口，不做 MiniApp |
| 子代理 / MBTI | `/personalization/subagents`、`/personalization/mbti` | subagents / MBTI API | owner | 中 | 表单 | ★★ 不做 |

### 2.2 账号级 / 平台级

| 功能 | 现有入口 | 主要 API | 权限 | 敏感度 | 桌面适配 |
|---|---|---|---|---|---|
| 偏好/语言/导航 | `/personalization` 以外：`Settings/Language`、preferences | `GET/PATCH /api/preferences` | 用户 | 低 | ★★ 原生即可 |
| 模型供应商 | `/models`、`/admin/models` | `GET /api/providers`、`GET /api/providers/presets` | `providers` | **极高**（`api_key`） | ★ 仅管理员只读状态，绝不代理原始 payload |
| 语音/搜索/Embedding/媒体生成 | `/models?tab=voice` 等 | `/api/voice/*`、`/api/search/*`、`/api/knowledge-bases/*` | 对应模块 | 高 | ★ 原生 |
| 安全/HITL/工具守卫 | `/admin/security` | `/api/admin/security/*` | `security` | 高 | ★ 原生；只读健康卡可选（P3） |
| 备份/存储/TLS/更新/环境变量 | `/admin/advanced`、`/admin/backend` | `/api/admin/backup/*`、`/api/admin/tls/*`、`/api/envs` | 对应模块 | 极高 | ★ 原生 |
| 用户/角色/邀请 | `/admin/users` | `/api/users/*` | `users` / `sso` | 高 | ★ 原生 |
| Bridge | `/bridge` | `/api/bridge/*` | 用户 | 中 | ★★ 状态卡（P3） |
| 外部事件 | `/external-events` | `/api/external-events/*` | 用户 | 中 | ★★ 投递健康卡（P3） |
| ACP | `/acp` | `/api/acp/*` | admin | 中 | ★ 原生 |
| 专家市场/团队 | `/experts`、`/teams/*` | experts/teams API | owner | 中 | ★★ 经营看板可另做，不做配置 MiniApp |

### 2.3 关键事实

- 专家桌面的空间由专家推导：`dashboard/src/pages/Apps/DesktopGrid.tsx:842`；每个可访问专家都有独立桌面（`docs/miniapp-overview.md:302`）。
- 桌面窗口由 `WINDOW_REGISTRY` 描述：`dashboard/src/pages/Apps/desktopWindows.ts:143`；新增窗口类型只需要扩这个注册表 + 渲染分支。
- 桌面 Dock 当前只汇总 MiniApp + 会话/网页窗口：`dashboard/src/pages/Apps/DesktopGrid.tsx:2259`。
- MiniApp 页面无 JWT、无网络，所有操作走宿主桥：`dashboard/src/components/MiniAppFrame/bridge.ts:84`、`dashboard/src/components/MiniAppFrame/capabilities.ts:25`。
- 现有系统模板只解决「创建普通 MiniApp」：`src/octop/infra/apps/system_templates/`（5 个模板），模板格式是 `manifest.json + schema.json + app.html`，不携带实时平台数据。
- `light_apps` 没有 `kind/source` 列（`src/octop/infra/db/migrations/025_light_apps.sql:5`），增加系统类型需要迁移 + ACL/发布/备份全链路适配。
- 部分高价值 API 目前**没有前端入口**：`/agents/{id}/metrics`、`/inbox`、`/autonomy-policy` 只在 API 层存在。系统应用可以直接把这些能力用起来。

---

## 3. 适配度评估

### 3.1 高适配（建议首批）

| 应用 | 为什么适合 |
|---|---|
| 用量与预算 | 纯聚合、只读、天然按专家过滤；用户打开桌面就能看到「这个专家今天花了多少」 |
| 连接（通道 + 连接器） | 用户最常问「为什么收不到消息/工具不可用」；状态卡比配置表单更有价值 |
| 能力（技能 + 工具/插件） | 列表/计数/开关，数据量小；打开桌面即可确认「这个专家会什么」 |
| 经营看板（指标/预算/待办） | `/metrics` + `/inbox` 已实现但无 UI；对数字员工定位价值最高（**已定：进 MVP**） |
| 记忆 | 统计 + 最近条目（P2 可选，可滑到 P3）；候选审核是轻操作（P3） |
| 任务 | 列表 + run-now；创建/编辑进桌面原生管理面板（P2） |

### 3.2 中适配（摘要 + 原生管理面板）

- 专家参数：只读展示模型、上下文、限额；编辑 Prompt/高级参数留在 `/agent-config`。
- 知识库：展示「绑定了几本、文档数、索引是否可用」；上传/重建索引留在 `/knowledge-bases`。
- 主动关怀：展示开关与最近运行状态；策略编辑留在原生页面。
- 工具/插件：展示启用数量与异常项；安装/卸载留在原生页面（P3 再评估开关）。

### 3.3 低适配（不建议做成系统应用）

- 连接器新增/编辑：OAuth 弹窗、凭证输入、自定义 MCP JSON；沙箱无法安全承载。
- 通道配置：扫码登录、BotCreator 子进程、AppSecret；只做状态卡。
- 子代理/MBTI：低频、复杂表单，桌面入口价值低。
- 模型供应商/安全/备份/存储/TLS/用户管理：密钥与全局基础设施，**不应进入前端沙箱**。

### 3.4 明确不做

- 不让 Agent 生成「配置看板 MiniApp」并写入 `light_app_rows`。
- 不把系统应用放进公开链接、看板分享、聊天侧栏或 IM 卡片。
- 不在沙箱里复制凭证表单、OAuth 回调、二维码轮询。
- 不为系统应用新增 `light_apps.kind` / 协作者 / 发布能力。

---

## 4. 集成方案对比

| 方案 | 做法 | 数据新鲜度 | 安全边界 | 复用现有桌面 | 后端改造 | 适用结论 |
|---|---|---|---|---|---|---|
| **A. 平台系统应用（推荐）** | 前端注册表 + 打包 HTML + `SystemAppFrame` + 新窗口类型；宿主桥白名单代理现有 API | 实时 | 强：不进 `light_apps`，无协作者/发布，能力固定 | 高 | **无**（P0/P1） | 首批与二期 |
| B. 扩展 `light_apps` 为 system kind | 加列/迁移；系统应用也走 app 列表、详情、看板；数据需同步到行表或新增数据源 | 需同步/调度，易过期 | 弱：协作者、导出、备份、公开链接都要额外封堵 | 最高 | 大 | 仅当需要分享/嵌入时再评估 |
| C. 原生 React 桌面窗口 | 新增 `console` 窗口类型，直接渲染现有设置面板 | 实时 | 最强（同源 React） | 中 | 无 | 重编辑场景的补充，不是 MiniApp |

### 4.1 为什么不让 Agent 生成配置 MiniApp

1. **数据扩散面**：`light_app_rows` 可被协作者读写、可导出、可备份、可发布成公开链接；即使只放「状态」，也会形成第二份敏感数据副本。
2. **数据必然过期**：用量按天/按模型聚合，通道状态实时变化；同步到行表需要数据源 + 调度 + 游标，复杂度不低，且用户看到的是旧值。
3. **权限模型不匹配**：配置是 owner-only，而 MiniApp 天生是协作/分享模型；把 owner-only 数据塞进协作文档，需要额外的行级/字段级 ACL，而 MiniApp 当前没有字段级权限。
4. **Agent 可变**：Agent 可以 `app_write_html`；系统应用 HTML 必须不可编辑、随产品版本发布。
5. **升级成本**：系统应用随产品迭代；每个应用在每个专家下都建一份 `light_apps` 记录，会让 `app_list`、备份、删除守卫、看板都背上无意义的对象。

> 如果未来确实需要「把某个配置看板分享给同事」，正确方向是**从系统应用导出/快照**成一个普通 MiniApp，而不是让系统应用本身变成可分享对象。

### 4.2 方案 A 的产品边界

| 维度 | 系统应用 | 普通 MiniApp |
|---|---|---|
| 宿主 | Agent 桌面（`kind="system"` 窗口） | 桌面/看板/聊天/公开链接 |
| 数据 | 宿主桥实时读现有 API | `light_app_tables` / `light_app_rows` |
| 创建者 | 平台（随版本发布） | Agent / 用户 |
| 可编辑 | 否（HTML 只读） | 是（`app_write_html`） |
| 协作/发布 | 否 | 是 |
| 权限 | 服务端现有权限 + 桌面可见性 | 应用 ACL + 能力位 |
| 离线/公开 | 否 | 公开链接只读 |

---

## 5. 推荐架构

### 5.1 运行时链路

```text
AppsPage / DesktopGrid（按专家空间）
  └─ SystemAppIcon（registry 可见性过滤）
        └─ DesktopWindow(kind="system", systemKey, agentId, spaceId)
              └─ SystemAppFrame
                    ├─ buildWrapperDoc(buildSrcDoc(html, systemSdk, locale))
                    ├─ attachBridge(capabilities=[system.read, system.manage])
                    │     ├─ systemQuery(key, params)
                    │     │     └─ queries.ts（白名单 + 字段投影）
                    │     │           └─ request()  ── JWT ──► 现有 /api
                    │     ├─ systemAction(key, params)   （P2，二次确认）
                    │     └─ openManager(key)            （打开桌面原生管理面板）
                    └─ 页面内 HTML/CSS/JS（无网络、无 JWT）
```

### 5.2 前端代码落点

| 文件 | 改动 |
|---|---|
| `dashboard/src/pages/Apps/system/registry.ts` | 系统应用定义：`key`、标题、图标、scope、所需权限、窗口最小尺寸、HTML 加载器 |
| `dashboard/src/pages/Apps/system/queries.ts` | `systemQuery` 白名单与字段投影（只返回安全字段） |
| `dashboard/src/pages/Apps/system/SystemAppFrame.tsx` | 复用沙箱 wrapper/CSP；固定能力集；处理 `systemQuery` / `systemAction` / `openManager` |
| `dashboard/src/pages/Apps/system/apps/*.html` | 单文件 HTML（每个应用一份，懒加载） |
| `dashboard/src/pages/Apps/desktopManager/DesktopManagerDrawer.tsx` | 原生管理抽屉：按 `openManager(key)` 渲染技能/工具/通道/连接器/任务面板，不加载 Console SPA |
| `dashboard/src/pages/Apps/desktopManager/DesktopConnectorsPanel.tsx` | 桌面版连接器管理：实例、目录、自定义 MCP、配置抽屉 |
| `dashboard/src/pages/Apps/desktopManager/DesktopTasksPanel.tsx` | 桌面版任务管理：列表、创建/编辑、启停、删除、立即运行 |
| `dashboard/src/components/MiniAppFrame/bridge.ts` | 给 `attachBridge` 增加可选 `extraMethods`（仅 `SystemAppFrame` 传入 `systemQuery` / `systemAction` / `openManager`）；**不把**系统方法加入全局 `KNOWN_METHODS` |
| `dashboard/src/components/MiniAppFrame/capabilities.ts` | 新增 `system.read` / `system.act` / `system.manage` 的 `METHOD_CAPABILITY` 映射；**不加入** `ALL_CAPABILITIES` / 普通应用默认能力集 |
| `dashboard/src/components/MiniAppFrame/sdk.ts` | `buildSrcDoc` 增加可选 SDK/locale 参数，默认行为不变 |
| `dashboard/src/pages/Apps/desktopWindows.ts` | `DesktopWindowKind` 增加 `"system"`；`WINDOW_REGISTRY` 增加 identity/尺寸/标题 |
| `dashboard/src/pages/Apps/DesktopGrid.tsx` | 注入系统应用图标、Dock 项、窗口渲染分支；建议抽 `useDesktopSystemApps` 控制 diff |
| `dashboard/src/pages/Apps/index.module.less` | 系统应用图标/角标样式 |
| `dashboard/src/locales/{zh,en}.json` | `apps.system.*` 标签 |

> 安全要点：`system.read` / `system.act` / `system.manage` **不要**加入前端 `ALL_CAPABILITIES`（`dashboard/src/components/MiniAppFrame/capabilities.ts:23`）或后端 `ALL_CAPABILITIES`（`src/octop/infra/apps/capabilities.py:32`）。前端 `effectiveCapabilities(undefined)` 会回退到 `ALL_CAPABILITIES`；只有把它们排除在外，`capabilities_json` 为空的旧版/普通应用才不会被默认放行。`SystemAppFrame` 必须**显式**传入固定的 `["system.read", "system.manage"]`（按应用声明追加 `system.act`）。

### 5.3 数据契约（P0/P1）

`agentId` 由窗口上下文注入，页面参数不能覆盖（避免跨专家读取；服务端仍会二次校验）。

| key | 宿主 API | 参数 | 投影字段（只返回这些） | 权限 |
|---|---|---|---|---|
| `usage.summary` | `GET /api/usage/summary` | `window`、`granularity`、`agent_id=ctx` | `total_tokens`、`input/output/cache`、`model_calls`、`turns`、`buckets` | `token_usage` |
| `agent.metrics` | `GET /api/agents/{id}/metrics` | `window` | `tasks`、`usage`、`budget` | 专家 owner |
| `agent.inbox` | `GET /api/agents/{id}/inbox` | — | `counts`、`items`（title/detail/since） | 专家 owner |
| `agent.status` | `GET /api/agents/{id}/status` | — | `state`、`last_error`、`channels`、`cron_jobs`、`memory_maintenance` | 专家 owner |
| `channels.list` | `GET /api/agents/{id}/channels` | — | `id`、`kind`、`name`、`enabled`、`runtime.connected`、`runtime.error` | owner + `channels` |
| `skills.list` | `GET /api/agents/{id}/skills` | — | `slug/name`、`description`、`enabled`、`kind` | `skills`（owner 可写） |
| `connectors.list` | `GET /api/connector-instances` | — | `instance_id`、`kind`、`display_name`、`status`、`has_credentials`、`shared` | `connectors` |
| `tools.summary` | `GET /api/agents/{id}/tool-settings` | — | `name`、`label`、`source`、`enabled`、`available`（不返回插件配置原文） | owner + `tools` |
| `memory.counts` | `GET /api/agents/{id}/memory/stats/counts` | — | 计数对象 | owner + `memory` |
| `cron.list` | `GET /api/agents/{id}/cron` | — | `cron_id`、`name`、`trigger`、`enabled`、`next_run_at`、`last_status` | owner / `tasks` |
| `knowledge.bases` | `GET /api/knowledge-bases` + `GET /api/agents/{id}` | — | `knowledge_base_id`、`name`、`doc_count`、`max_documents`（按 `knowledge_base_ids` 过滤） | `knowledge_bases` |
| `agent.config` | `GET /api/agents/{id}` | — | `default_model`、`temperature`、`max_tokens`、`context`、`knowledge_base_ids`（**不返回 `system_prompt`**） | 专家 owner |

**投影规则**：
- 凭证类字段永不返回：`api_key`、`config`、`credentials`、`credentials_preview`、`system_prompt`、自定义 MCP JSON。
- 列表接口的 `runtime.error` 可以返回（这是用户排障最需要的），但需要截断长度。
- 所有字符串来自服务端现有 i18n；宿主 `request()` 已带 `Accept-Language`。

### 5.4 可见性规则

| 场景 | 是否显示 | 规则 |
|---|---|---|
| 专家 owner | ✅ | 专家可访问 + 对应模块权限 |
| 被共享专家（非 owner） | ❌（MVP） | 配置是 owner-only；共享者继续用聊天/业务 MiniApp |
| 管理员 | ✅ | 与其他用户一致：只显示自建/共享专家；不提供跨账号浏览模式 |
| 无对应模块权限 | ❌ | 例如无 `channels` 权限则不显示「连接」应用 |
| 团队 host | ❌ | 团队配置有自己的页面；MVP 不显示专家级配置应用 |
| 专家未启用/不可访问 | ❌ | 桌面空间本身不生成 |

> 客户端过滤只是 UX；真正的权限由现有 API 校验（`require_agent_owner_row` 等，`src/octop/api/common/agent.py`）。不要因为应用隐藏就假设数据安全。

### 5.5 刷新与休眠

- 打开窗口：拉一次；手动「刷新」按钮。
- 窗口可见且聚焦：60 秒轮询（通道状态 30 秒）；窗口最小化/切空间/失焦：暂停。
- 复用桌面现成的 `active` / `foreground` / `IFRAME_WINDOW_KINDS` 休眠机制（`docs/miniapp-overview.md:327`）；P0 可以先只做「打开时拉取 + 手动刷新」，轮询在 P1 补。
- 不做 WebSocket/SSE：配置变化不要求秒级；桌面已有通知中心负责「需要人处理」的事件。

### 5.6 i18n 与主题

- 应用标题/描述走 dashboard 的 `apps.system.*`（`dashboard/src/locales/{zh,en}.json`）。
- HTML 内文案由每个应用自带的 `STRINGS = {zh, en}` 选择；宿主通过 `buildSrcDoc` 注入 `document.documentElement.lang`。
- 服务端返回的标签（通道类型、工具名、错误消息）继续走现有 i18n（`Accept-Language`）。
- 主题：沿用 MiniApp 宿主样式（透明背景、`--fn-*` 变量不可用，需在 HTML 内用 CSS 变量或 `prefers-color-scheme`）。P1 统一定义一套 `--sys-*` 颜色变量注入。

### 5.7 原生管理与编辑

- `openManager(key)` 只允许应用注册表声明的原生管理面（`skills` / `tools` / `channels` / `connectors` / `tasks`）。
- 打开前由宿主把当前专家设为 active agent（`AgentContext` 的 `setActiveAgent`，`dashboard/src/context/AgentContext.tsx:497`），然后打开桌面原生管理抽屉。
- 管理面在桌面 React 树内实现（`DesktopManagerDrawer`），复用可嵌入的功能面板（`SkillsTabs` / `ToolsTabs` / `ChannelsPanel`）以及桌面定制的连接器、任务面板；**不加载、也不内嵌** Console SPA 页面。
- P2 的轻操作仍走现有 API，不新增「配置写入」专用网关。

管理入口审计（2026-11-17）：

| 系统应用 | 原控制台链接 | 现原生入口 |
|---|---|---|
| 能力 | `/personalization/skills` | `openManager("skills")` → `SkillsTabs` |
| 能力 | `/personalization/tools` | `openManager("tools")` → `ToolsTabs` |
| 连接 | `/personalization/channels` | `openManager("channels")` → `ChannelsPanel` |
| 连接 | `/connectors` | `openManager("connectors")` → `DesktopConnectorsPanel` |
| 任务 | `/tasks` | `openManager("tasks")` → `DesktopTasksPanel` |
| 经营看板 | `/tasks` | `openManager("tasks")` → `DesktopTasksPanel` |
| 用量与预算 | — | 无管理入口 |

### 5.8 窗口与 Dock

- 新窗口类型 `system`，identity：`system:<agentId>:<appKey>`。
- 窗口位置/大小/最小化/最大化沿用 `localStorage` 持久化（`WINDOWS_STORE_KEY`，`dashboard/src/pages/Apps/desktopWindows.ts`）；旧数据因 registry 校验会自动忽略未知类型，向后兼容。
- Dock：系统应用**只在运行时**作为窗口项出现，不默认全部 pin，避免 Dock 被配置入口占满；桌面网格固定展示（最多 6 个）。
- Mission Control / 命令面板自动包含系统窗口（它们从 `windows` 渲染）。

---

## 6. 应用设计

### 6.1 用量与预算（P1，旗舰）

**数据**：`agent.metrics`（任务/用量/预算）+ `usage.summary`（按天/按模型）。

**布局**：

```text
┌──────────────────────────────────────────────┐
│ 用量与预算            [今日|7天|30天]  [刷新] │
├──────────────┬──────────────┬────────────────┤
│ 总 Token     │ 模型调用     │ 预算使用        │
│ 1.24M        │ 386          │ 62% ██████░░    │
├──────────────┴──────────────┴────────────────┤
│ 每日趋势（SVG 柱/折线）                       │
├──────────────────────────────────────────────┤
│ 按模型：input / output / cache 命中率         │
│ 按任务：完成 / 进行中 / 失败                   │
└──────────────────────────────────────────────┘
```

**空态**：无用量时显示「这个专家还没有产生模型调用」，不额外提供写操作（P1 的 SystemAppFrame 不挂 `chat.compose`）。
**失败态**：显示服务端错误 + 重试；不要显示空白。

### 6.2 连接（P1）

**数据**：`channels.list` + `connectors.list`。

**布局**：

```text
┌──────────────────────────────────────────────┐
│ 连接                        [刷新]           │
├──────────────────────────────────────────────┤
│ 通道（3）                                     │
│  ● 企业微信 已连接    ● Telegram 未连接       │
│  ○ 飞书 未配置        [管理通道 →]            │
├──────────────────────────────────────────────┤
│ 连接器（5）                                   │
│  ● 高德地图 已授权    ● 飞书文档 已授权       │
│  ○ 企业微信 待授权    [管理连接器 →]          │
└──────────────────────────────────────────────┘
```

**规则**：只显示状态、名称、类型；不显示 AppSecret/Token/回调地址。未连接项按错误优先排序。
**管理**：`openManager("channels")` / `openManager("connectors")`，在桌面原生管理抽屉中复用 `ChannelsPanel` 与连接器卡片/配置抽屉。

### 6.3 能力（P1）

**数据**：`skills.list` + `tools.summary`。

**布局**：

```text
┌──────────────────────────────────────────────┐
│ 能力                        [刷新]           │
├──────────────────────────────────────────────┤
│ 技能（12）  已启用 10  已停用 2               │
│  ▸ 财务报表分析   ▸ 竞品调研   ▸ 小红书文案   │
│  [管理技能 →]                                 │
├──────────────────────────────────────────────┤
│ 工具（28）  内置 24 / 插件 4                  │
│  已禁用：browser、terminal（可点击查看）       │
│  [管理工具 →]                                 │
└──────────────────────────────────────────────┘
```

**规则**：技能列表分页/折叠；不返回 SKILL.md 全文。P2 可加开关（`POST /agents/{id}/skills/{name}/enable|disable`）。

### 6.4 经营看板（P1，MVP）

**数据**：`agent.metrics` + `agent.inbox` + `agent.status` + `autonomy-policy`。

**价值**：这是「数字员工」定位的核心桌面入口，且 API 已存在但无 UI。展示：

- 今日/本周任务完成、进行中、失败、逾期；
- Token 预算使用与剩余；
- 待处理审批/失败任务/预算耗尽（`inbox.counts`）；
- 自主性状态与工作时段（MVP 只读；开关操作不在 P2 轻操作范围内）。

### 6.5 记忆 / 任务 / 知识（P2/P3）

- **记忆**：counts + growth + atom kinds + 最近 journal（P2 可选，可滑到 P3）；candidates 审核列表 + 批准/拒绝留到 P3。
- **任务**：cron 列表 + 下次运行 + 最近状态 + 「立即运行」（P2）；创建/编辑通过 `openManager("tasks")` 打开桌面任务管理面板。
- **知识**：绑定的 KB 列表 + doc_count/max_documents + 索引可用性（P3）；上传/重建深链 `/knowledge-bases`。
- **专家参数**：模型/上下文/限额摘要（P3）；Prompt 编辑深链 `/agent-config`。

---

## 7. 落地计划

### Phase 0 — 地基（步行骨架，约 4–6 人日）

**目标**：一个系统应用能在专家桌面打开，且有完整安全边界。

- [ ] `registry.ts` + 可见性纯函数（owner/shared/admin/permission）。
- [ ] `SystemAppFrame`：复用 `buildWrapperDoc` / `buildSrcDoc`，注入受限 SDK；固定能力集。
- [ ] 新增 `system` 窗口类型（identity/尺寸/标题/持久化/休眠）。
- [ ] `systemQuery` 桥 + 白名单 + 字段投影；`openManager` 原生管理面。
- [ ] 一个试点应用：**用量**（只读，`usage.summary`）。
- [ ] 单测：可见性矩阵、投影不含凭证、普通 MiniApp 调用 `systemQuery` 被拒、窗口恢复。

**验收**：专家 owner 在桌面看到「用量」；共享用户看不到；页面请求 payload 中无 `api_key/config/credentials`；`make all` 绿。

### Phase 1 — MVP 四应用（约 7–10 人日）

> 已确认（2026-11-17）：四个**独立应用**——「用量与预算」「连接」「能力」「经营看板」，各自一个桌面图标/窗口；不做聚合控制台，聚合入口只作为 P3 的启动器，不合并应用。

- [ ] 「用量与预算」：`agent.metrics` + `usage.summary`，趋势图 + 预算条。
- [ ] 「连接」：通道 + 连接器状态，错误优先。
- [ ] 「能力」：技能 + 工具/插件数量与禁用项。
- [ ] 「经营看板」：`agent.metrics` + `agent.inbox` + `agent.status` + autonomy 状态。
- [ ] 刷新/空态/错误态/原生管理入口；zh/en 文案。
- [ ] Dock/命令面板接入；桌面最多 6 个系统应用。
- [ ] 前端测试 + 手工权限矩阵。

**验收**：四个应用在 owner 桌面可用；数据与 `/token-usage`、`/personalization/channels`、`/personalization/skills`、`/agents/{id}/metrics` 一致；共享用户/无权限用户不可见；`cd dashboard && npx tsc -b` 与 `make all` 绿。

### Phase 2 — 技能、任务与轻操作（约 5–8 人日）

> 已确认（2026-11-17）：P2 优先做**技能**与**任务**；记忆统计可与任务同批，也可滑到 P3。

- [ ] 技能启停（在「能力」应用内）。
- [ ] 「任务」应用：cron 列表 + 下次运行 + 最近状态 + 「立即运行」。
- [ ] `systemAction` 桥：白名单 + 二次确认 + 错误提示 + 审计。
- [ ] 「记忆」只读统计（counts / growth / atom kinds / 最近 journal；可滑到 P3）。
- [ ] 操作后刷新对应应用，并与原生管理面板数据一致。

**验收**：每个操作都能在原生管理面板看到相同结果；失败路径有明确提示；无权限用户无法通过桥调用（服务端 403）；新增测试覆盖。

### Phase 3 — 平台化（可选，约 6–10 人日）

- [ ] 「我的桌面」空间：账号级用量、偏好、Bridge、外部事件健康。
- [ ] 管理员系统应用：模型/安全/备份**只读健康卡**（绝不代理密钥）。
- [ ] 「控制台」启动器（可选）：卡片式入口，优先打开各自独立的系统应用/原生页面；不把四个应用合并成标签页。
- [ ] 延后操作：通道启停、记忆候选批准/拒绝、工具/插件开关（P2 未纳入，P3 再评估）。
- [ ] 评估后端系统应用注册表（为 PWA/移动端/IM 复用），或维持前端注册表。
- [ ] 评估桌面布局服务端持久化（当前仅 `localStorage`，`docs/miniapp-overview.md:329`）。

### 依赖与顺序

```text
P0 地基 ──► P1 用量/连接/能力/经营看板 ──► P2 技能&任务轻操作（记忆可滑档） ──► P3 个人空间/管理端/延后操作
```

### PR 拆分建议

1. `feat(desktop): system app registry and sandbox frame`（不暴露应用）
2. `feat(desktop): system window kind and desktop integration`
3. `feat(desktop): usage system app`（首个可见应用）
4. `feat(desktop): connections system app`
5. `feat(desktop): capabilities system app`
6. `feat(desktop): operations dashboard system app`（MVP 第四个应用）
7. `feat(desktop): skill toggle and task run-now actions`（P2 轻操作）
8. `feat(desktop): tasks system app`（P2；记忆统计可同批或单独 PR）
9. `docs: system apps plan and security notes`

每个 PR 自带单测；P0 的桥/投影测试必须先于第一个可见应用合入。

### 工作量说明

- 按 1 名熟悉 DesktopGrid / MiniAppFrame 的工程师估算；不含产品设计、视觉稿、完整回归。
- P0+P1 约 **3–4 周**；P2 约 **1.5–2.5 周**；P3 约 **1–2 周**。两人并行时 P1 的四个应用可分摊。
- 最大不确定项是 DesktopGrid 的集成 diff（约 3000 行）和沙箱 SDK 参数化；建议先用 `useDesktopSystemApps` / 独立组件隔离，避免大改。

---

## 8. 验证与验收

### 8.1 自动化

```bash
# 后端全量（本轮无后端改动也应保持绿）
make all

# 前端类型检查
cd dashboard && npx tsc -b

# 前端单测（使用本地二进制，避免 npx 拉到缓存版本）
cd dashboard && ./node_modules/.bin/vitest run src/pages/Apps/system src/pages/Apps/desktopWindows.test.ts

# 构建 dashboard 产物（pre-commit 也会执行）
make build-frontend
```

建议新增测试：

- `registry.test.ts`：owner / shared / admin / 无模块权限 / 团队 host 的可见性矩阵。
- `queries.test.ts`：每个 key 的投影只含白名单字段；`api_key`、`config`、`credentials`、`system_prompt` 永不出现。
- `bridge.test.ts`：普通 MiniApp 无 `system.read` 时 `systemQuery` 被拒；未知 key 被拒；key 只能读当前 `agentId`。
- `desktopWindows.test.ts`：`system` kind 的 identity、恢复、标题、最小尺寸。
- `DesktopGrid.system.test.tsx`：系统应用出现在正确专家空间；Dock 仅显示运行中的系统窗口。
- `SystemAppFrame.test.tsx`：HTML 懒加载、错误态、`openManager` 注入；`registry.test.ts` 覆盖 manager 白名单。
- `operations.test.ts`：经营看板的 metrics/inbox/budget 投影与空态/错误态。

### 8.2 手工矩阵

| 场景 | 期望 |
|---|---|
| 专家 owner | 看到有权限的系统应用；数据与控制台一致 |
| 经营看板 | 指标/预算/待办与 `/agents/{id}/metrics`、`/inbox` 一致；预算耗尽或失败任务醒目 |
| 共享专家（非 owner） | 不显示配置类系统应用 |
| 管理员 | 只能看到自己的和共享的专家桌面与小程序，与普通用户一致 |
| 无 `channels` 权限 | 不显示「连接」应用 |
| 无 `token_usage` 权限 | 不显示「用量」应用 |
| 最小化/切空间 | 停止轮询；超过 30 分钟卸载 iframe（沿用现有休眠） |
| 刷新桌面 | 系统窗口位置/大小恢复；旧 `localStorage` 不报错 |
| 公开链接/聊天侧栏 | 不出现系统应用 |
| 网络面板 | 无 `api_key`、`credentials`、`config`、`system_prompt` |
| 只读用户调用写操作（P2） | 服务端 403；UI 提示；不静默失败 |

### 8.3 安全清单

- [ ] 系统应用 HTML 随前端发布，用户/Agent 不可写。
- [ ] `system.read` / `system.act` / `system.manage` 不在前端或后端 `ALL_CAPABILITIES`，普通/旧版 MiniApp 永远拿不到。
- [ ] `SystemAppFrame` 能力固定；不挂 `data.read`、`external.fetch`、`service`、`generate`。
- [ ] 所有查询走现有 API + 现有权限校验；前端只做可见性过滤。
- [ ] 字段投影白名单；凭证/Prompt 永不返回。
- [ ] `openManager` 目标白名单；打开桌面原生管理抽屉，不加载 Console SPA、不新开浏览器标签页。
- [ ] 系统应用 HTML 不含 `<a href>` / `window.open` / 顶层 `location` 跳转；所有管理入口统一走 `octopSystem.openManager`。
- [ ] 系统应用不在公开链接/看板/聊天/MiniApp 列表中暴露。
- [ ] 轻操作（P2）二次确认 + 审计；不新增绕过现有权限的写接口。

### 8.4 度量

| 指标 | 含义 | 期望 |
|---|---|---|
| 系统应用周打开次数 / 打开用户数 | 是否真的被用 | 上升 |
| 从系统应用打开原生管理面板的转化 | 摘要是否驱动了配置动作 | 上升 |
| 通道/连接器异常被发现的时间 | 桌面状态卡是否提前暴露问题 | 下降 |
| 预算超限次数 / 超限前的查看率 | 用量可见性是否有效 | 超限下降、查看率上升 |
| 桌面打开到首屏的时间 | 系统应用是否拖慢桌面 | 不劣化（P95） |
| 配置类工单/提问量 | 「去哪里配置」是否减少 | 下降 |

---

## 9. 风险与开放问题

### 9.1 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 系统应用 HTML 与原生管理面板重复 | 两套界面不一致 | 系统应用只做摘要；编辑统一走 `DesktopManagerDrawer` 复用原生面板 |
| 权限判断漏项 | 越权读取 | 客户端隐藏 + 服务端校验 + 可见性/投影单测 |
| 凭证误入投影 | 安全事故 | 白名单投影 + 测试断言 + Code Review 清单 |
| DesktopGrid diff 过大 | 与上游冲突、回归 | 独立 registry/frame/hook；渲染分支集中；不改现有 MiniApp 路径 |
| 桌面图标过多 | 体验变差 | 最多 6 个；Dock 只显示运行中；P3 聚合入口 |
| 轮询过多 | 服务端压力 | 可见才轮询、60s、手动刷新；后续可加聚合端点/缓存 |
| i18n 遗漏 | 中英不一致 | 每个应用自带 `STRINGS`；P1 加 key parity 测试 |
| 未来需要分享 | 方案不满足 | 评估「快照导出为普通 MiniApp」，不把系统应用变成可分享对象 |

### 9.2 决策记录与待确认

1. **已定（2026-11-17）：MVP 采用 4 个独立应用**——「用量与预算」「连接」「能力」「经营看板」，各自一个桌面图标/窗口；不做「专家控制台 + 标签页」聚合。P3 可以加一个启动器聚合入口，但底层仍是独立应用。
2. **已定（2026-11-17）：经营看板进 MVP。** `/metrics` + `/inbox` 已实现但无 UI，作为第四个独立应用与前三者并列，不合并。
3. **已定（2026-11-17）：P2 轻操作先做技能启停 + 任务立即运行。** 通道启停、记忆候选审核、工具/插件开关延后到 P3 再评估。
4. **账号级应用放哪里**：新增「我的桌面」空间，还是只放「全部应用」空间？
5. **管理员应用是否要做**：只读健康卡有价值，但容易变成「半个管理台」；建议 P3 再评估。
6. **长期是否统一到 `light_apps`**：只有当「系统应用需要被分享/嵌入 IM/公开」时才值得；当前明确不做。
7. **命名**：产品文案用「系统应用」「桌面组件」还是「专家控制台」？建议 UI 用「系统应用」，文档用 System App。

### 9.3 明确不做（本计划内）

- 不做系统应用的公开链接、看板分享、聊天侧栏嵌入。
- 不做凭证/OAuth/扫码/文件上传的沙箱内表单。
- 不做 Agent 生成/修改系统应用。
- 不做模型供应商、安全、备份、存储、TLS、用户管理的写操作。
- 不重复做通知中心/审批中心（桌面已有 `DesktopNotificationCenter`；经营看板只取 inbox 摘要）。
- 不新增平台级 EventBus、第二套权限或第二套发布机制。

---

## 10. 代码索引

| 主题 | 位置 |
|---|---|
| 桌面模式现状 | `docs/miniapp-overview.md:302` |
| 桌面空间推导 | `dashboard/src/pages/Apps/DesktopGrid.tsx:842` |
| 桌面窗口注册表 | `dashboard/src/pages/Apps/desktopWindows.ts:143` |
| Dock 汇总 | `dashboard/src/pages/Apps/DesktopGrid.tsx:2259` |
| MiniApp 桥方法 | `dashboard/src/components/MiniAppFrame/bridge.ts:84` |
| 能力位映射 | `dashboard/src/components/MiniAppFrame/capabilities.ts:25` |
| 沙箱 wrapper / CSP | `dashboard/src/components/MiniAppFrame/wrapper.ts`、`appCsp.ts` |
| SDK 注入 | `dashboard/src/components/MiniAppFrame/sdk.ts:352` |
| 后端能力清单 | `src/octop/infra/apps/capabilities.py:32` |
| `light_apps` DDL | `src/octop/infra/db/migrations/025_light_apps.sql:5` |
| 用量 API | `src/octop/api/routers/usage.py:215` |
| 经营指标 / 待办 | `src/octop/api/routers/autonomy.py:294`、`:217` |
| 专家状态 | `src/octop/api/routers/agents.py:580` |
| 通道 | `src/octop/api/routers/channels.py:156` |
| 技能 | `src/octop/api/routers/skills.py:486` |
| 工具设置 | `src/octop/api/routers/agent_tools.py:158` |
| 连接器 | `src/octop/api/routers/connectors.py:555` |
| 记忆统计 | `src/octop/api/routers/memory.py:551` |
| 定时任务 | `src/octop/api/routers/cron.py:118` |
| 知识库 | `src/octop/api/routers/knowledge_bases.py:525` |
| 权限目录 | `src/octop/infra/users/permissions.py`、`dashboard/src/utils/permissions.ts` |
| 专家归属校验 | `src/octop/api/common/agent.py` |
| 系统模板（普通 MiniApp） | `src/octop/infra/apps/system_templates/` |

---

## 11. 实现进度（2026-11-17）

### 已落地（P0 + P1 + P2 技能/任务）

- **系统应用注册表**：`dashboard/src/pages/Apps/system/registry.ts`（5 个应用、owner/admin/权限可见性、每应用查询与写操作白名单）。
- **沙箱宿主**：`dashboard/src/pages/Apps/system/SystemAppFrame.tsx` + `systemSdk.ts`；复用 MiniApp wrapper/CSP，按应用授予 `system.read` / `system.act` / `system.manage`，注入受限 SDK。
- **桥安全**：系统能力不进入前端/后端 `ALL_CAPABILITIES`；普通/旧版 MiniApp 无法调用系统方法（`bridge.ts` 的 `extraMethods`）。写操作经 `Modal.confirm` 二次确认。
- **窗口类型**：`desktopWindows.ts` 新增 `system` kind（identity、尺寸、持久化、标题）；`DesktopGrid` 接入桌面图标、Dock、命令面板、Mission Control。
- **五个应用**：`usage.html`、`connections.html`、`capabilities.html`、`operations.html`、`tasks.html`；均通过宿主桥读取现有 API，不落 `light_apps`。
- **P2 轻操作**：技能启停（`skills.toggle`）、任务立即运行（`cron.run`）已接入现有 POST 端点；通道启停、记忆审核仍未开放。
- **可见性收口**：普通用户与管理员一致，只显示自建/共享专家的桌面与其可用 MiniApp；服务端 `AppService.list_for_user` 同步收窄，管理员不再列出全部应用。
- **桌面组织**：系统应用收进一个文件夹；新增「管理桌面」抽屉（按专家隐藏系统应用、隐藏用户 MiniApp、固定/取消固定 Dock、重置）；组织偏好存 `localStorage`（`octop:apps-desktop-org:v1`）。详见 [桌面小程序组织](./desktop-miniapp-organization.md)。
- **桌面组织二期**：图标拖拽排序；自定义文件夹（创建、重命名、增删应用、删除，应用只能属于一个文件夹）；「全部应用」保持纯 MiniApp 库，系统应用不按专家重复展示。
- **数据投影**：`queries.ts` 只返回白名单字段；通道/连接器/技能/工具/指标/待办/定时任务均已接入；深链走 `openManager` + 桌面原生管理抽屉，不加载原网页界面、不新开浏览器标签页。
- **管理入口收口**：五个系统应用的全部管理入口（技能、工具、通道、连接器、任务）统一走 `octopSystem.openManager`，由 `DesktopManagerDrawer` 在桌面内渲染原生管理面板；HTML 内不存在 `<a href>`、`window.open` 或顶层 `location` 跳转，`apps.test.ts` 对此做静态回归检查。
- **测试**：系统应用/桌面/桥相关 **98 项**通过（`SystemAppFrame`、`registry`、`queries`、`actions`、`desktopWindows`、`DesktopGrid.system`、`MiniAppFrame`）；五个 HTML 内联脚本 `node --check` 通过。
- **生产构建**：`NODE_OPTIONS=--max-old-space-size=3072 npm run build` **通过**（本机约 27 分钟）；产物包含 `usage` / `connections` / `capabilities` / `operations` / `tasks` 五个异步 chunk 与 `SystemAppFrame`。

### 下一步

- 记忆只读统计（P2 可选）。
- 通道启停、记忆候选审核（P3 再评估）。
- 生产构建与手工权限矩阵完成后，补一轮桌面端真实数据核对。
