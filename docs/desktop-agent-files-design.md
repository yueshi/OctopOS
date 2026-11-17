# 桌面端 Agent 生成文件与 HTML 浏览 — 系统设计

Status: 设计提案（P0 + 独立 HTML 窗口已实现，2026-11-16）
Date: 2026-11-16
Scope: Agent OS 桌面（`/apps?view=desktop`，`dashboard/src/pages/Apps/`）
相关文档：`docs/agent-os-roadmap.md`、`docs/miniapp-overview.md`、`docs/agent-backend-file-io.md`

> 本文里的「桌面」指工作台里的 Agent OS 桌面视图（专家桌面、窗口、Dock）。
> 已安装的桌面壳 / PWA 运行的是同一个 SPA，因此本设计同样适用。

---

## 0. 结论

桌面已经具备大部分原语：工作区文件 API、会话产物账本、`FileViewer`、窗口注册表、
MiniApp 的沙箱渲染。缺的不是底层能力，而是**把「生成文件」做成桌面的一级对象**：

1. **新增 `files` 窗口**：每个专家一个，默认打开「生成文件」，可切换到「全部文件」，
   右侧直接预览；只读，不提供完整工作区抽屉，浏览与预览都在桌面窗口内完成。
2. **生成文件以会话产物账本为准**：复用 `threads.artifacts`（`write_file` / `edit_file` /
   `send_file` / 截图等工具自动记录），而不是扫描文件系统猜「哪些是生成的」。
3. **HTML 用能力 URL 浏览**：新增短期、只读、按目录授权的 preview token，
   让 `iframe` 能加载 HTML 及其相对 CSS/JS/图片；沙箱 + CSP 保证生成页面碰不到
   控制台的 JWT / DOM。自包含 HTML 可用 `srcDoc` 兜底。
4. **聊天与桌面接线**：桌面聊天窗补上 `ChatFilePreviewProvider`，文件卡片从「不能预览」
   变为「在文件窗口打开」；聊天标题栏增加「文件」入口。
5. **独立 HTML 窗口（P1 已实现）**：每个 HTML 文件一个独立窗口（`html:<agentId>:<path>`），
   聊天文件卡片、生成文件列表和工作区树里的 HTML 默认打开独立窗口，可并排查看多份
   图表/打卡页面；文件窗口内保留「在此预览」入口。

不需要数据库迁移；后端只需一个产物查询接口、一个 preview token 模块、一条预览路由，
外加一处 `workspace/tree` 的运行状态放宽。建议按 P0（可用闭环）→ P1（体验与安全加固）
→ P2（通知/移动端/导出）推进。

---

## 1. 现状盘点

### 1.1 桌面窗口模型

| 事实 | 位置 |
|------|------|
| 窗口类型只有 `app` / `chat` / `external` | `dashboard/src/pages/Apps/desktopWindows.ts:17` |
| 每种窗口的 id、最小尺寸、标签、校验集中在注册表 | `dashboard/src/pages/Apps/desktopWindows.ts:65` |
| 窗口状态持久化到 `localStorage`，恢复时硬编码校验三种 kind | `dashboard/src/pages/Apps/desktopWindows.ts:263` |
| 渲染分支：MiniApp / 外部网页 / 聊天 | `dashboard/src/pages/Apps/DesktopGrid.tsx:2420`、`:2443`、`:2450` |
| Dock、任务中心、命令面板都从注册表取图标和标签 | `DesktopDock.tsx`、`DesktopMissionControl.tsx`、`DesktopGrid.tsx:1584` |

这意味着新增窗口类型的成本主要在**注册表 + 持久化校验 + 渲染分支 + 入口**，
其余 UI（任务中心、Dock 菜单、标题）可以自动继承。

### 1.2 文件浏览与预览

| 能力 | 现状 |
|------|------|
| 目录/文件 API | `GET /api/agents/{id}/workspace/tree`、`/file`、`/download`、`/upload`、`/doc`、`/media/preview`（`src/octop/api/routers/workspace.py`） |
| 目录列表要求 agent 运行中 | `workspace/tree` 用 `require_running_workspace`（`workspace.py:131`） |
| 文件读取/下载在 agent 停止时也可用 | `read_file` / `download` 用 `require_agent_workspace`（`workspace.py:166`、`:346`） |
| 完整工作区 UI | `WorkspaceDrawer`（树 + 预览 + 编辑 + 上传/删除/移动），可 `embedded` 嵌入聊天 Dock（`ChatDockPanel.tsx:623`） |
| 统一文件渲染 | `FileViewer`：媒体 / 文档 / Markdown / HTML / 源码 / Monaco（`pages/Agent/Workspace/components/FileViewer.tsx`） |
| HTML 预览 | `FilePreview` 用 `iframe srcDoc` + `sandbox="allow-scripts allow-popups allow-forms"`（`FilePreview.tsx:107`） |
| 聊天文件卡片 | `MessageFileCard` 仅在存在 `ChatFilePreviewContext` 时显示预览按钮（`MessageFileCard.tsx:42`、`:48`） |
| 桌面聊天没有该 Context | `ChatFilePreviewProvider` 只包在 Chat 页（`pages/Chat/index.tsx:1492`）；`DesktopChatWindow` 直接渲染 `MessageList`（`DesktopChatWindow.tsx:969`） |

### 1.3 生成文件账本

| 事实 | 位置 |
|------|------|
| 产物工具白名单 | `write_file` / `edit_file` / `send_file` / `send_file_to_user` / `desktop_screenshot` / `mobile_screenshot`（`src/octop/infra/agents/threads/artifact.py:23`） |
| 产物按 thread 存 `threads.artifacts` | 同上，`normalize_artifact_path` / `thread_artifacts_payload` |
| 线程列表已返回 `artifacts` + `artifact_refs` | `GET /api/agents/{id}/threads`（`api/routers/chat/history.py:152`） |
| Chat 页用会话产物生成 Dock 文件列表 | `pages/Chat/index.tsx:668` |
| 产物带 `agent_id`（团队/子代理可区分生产者） | `ThreadArtifact { path, agent_id }` |

**结论**：生成文件不需要新表；`threads.artifacts` 就是权威来源，并且已经带会话/生产者上下文。

### 1.4 可复用的安全先例

| 先例 | 用法 |
|------|------|
| MiniApp HTML 渲染 | `GET /api/apps/{id}/html` 返回 `Content-Security-Policy: sandbox`（路由 `api/routers/apps.py:1805`，响应头 `:1821`）；前端 `MiniAppFrame` 用 wrapper + `sandbox="allow-scripts"` |
| MiniApp 页面 CSP | `default-src 'none'; script-src 'unsafe-inline'; …; connect-src 'none'`（`infra/apps/sandbox.py:31`） |
| 窄作用域短期 token | 游戏结果回写 token：`typ` + 单 app/user/session + TTL（`infra/apps/game_sessions.py`） |
| JWT 豁免白名单 | `/api/game-sessions/`、`/api/public/apps/` 等以 token 为凭据（`api/deps.py:32`） |
| 媒体预览沙箱头 | `Content-Security-Policy: sandbox`（`api/routers/workspace.py:462`） |

preview token 直接沿用「独立 `typ`、同一 jwt secret、JWT 中间件豁免」的成熟模式。

---

## 2. 缺口

| # | 缺口 | 证据 | 用户感受 |
|---|------|------|----------|
| 1 | 桌面没有文件窗口 | 窗口注册表只有 app/chat/external | 在桌面里看不到专家产出的文件，必须离开桌面去「个人化 → 工作区」 |
| 2 | 桌面聊天文件卡片没有预览 | `DesktopChatWindow` 未包 `ChatFilePreviewProvider` | 同一张文件卡片在 Chat 页能预览，在桌面聊天里连「眼睛」按钮都不出现 |
| 3 | HTML 只能 `srcDoc` 预览 | `FilePreview.tsx:122` | 引用相对图片/CSS/JS 的报告会缺资源；没有 URL，不能独立窗口打开 |
| 4 | 产物账本没有在桌面暴露 | 只有 Chat 页用 `composerSession.artifacts` | 「这个专家最近生成了什么」不可见 |
| 5 | 专家停止后目录列表不可用 | `workspace/tree` 要求运行中 | 历史文件看不了；但文件其实还在 |
| 6 | 没有生成后的刷新/提示 | 无桌面级事件 | 聊天里刚生成文件，文件窗口不会自动更新 |

---

## 3. 目标与非目标

### 目标

1. 在桌面内查看某个专家**本次会话 / 最近会话**生成的文件，并可直接预览、下载。
2. 安全地渲染 Agent 生成的 HTML：允许内联脚本（交互式报告），但默认禁止外部网络，
   绝不暴露控制台 JWT / localStorage / 父 DOM。
3. 从聊天文件卡片、桌面 Dock、命令面板都能一步到达文件；HTML 是文件，不是 MiniApp。
4. 专家停止时仍可只读浏览历史文件。
5. 不引入新存储、新表或第二套文件服务。

### 非目标

- 不在桌面文件窗口中做编辑、上传、删除、移动；桌面内不提供完整工作区抽屉。
- 不把生成的 HTML 自动注册为 MiniApp（两者数据模型和发布语义不同）。
- 不做文件版本库、协同编辑、对外分享链接（公开链接沿用 MiniApp publication）。
- 不改变现有 Chat 页 / 工作区页的行为（只是复用与增强）。

---

## 4. 用户场景

**S1 生成报告后立刻看**
专家在桌面聊天里生成 `outbound/销售周报.html` 和 `outbound/chart.png`。
聊天消息出现文件卡片 → 点「预览」→ 文件窗口打开「生成文件」并选中报告 →
右侧 iframe 渲染 HTML，`chart.png` 按相对路径加载。

**S2 从 Dock 回看**
第二天切到该专家桌面 → Dock 点「文件」→ 默认「生成文件」列表 →
按会话分组看到昨天、今天的产物 → 点 PDF 直接预览/下载。

**S3 浏览整个工作区**
在文件窗口切到「全部文件」→ 懒加载目录树 → 打开 `outbound/` 下的图片、文档 →
需要编辑/上传时使用控制台侧栏的 Agent 工作区页面（不在桌面里弹出抽屉）。

**S4 专家已停止**
专家离线 → Dock「文件」仍可打开 → 顶部显示「专家已停止 · 只读」→
生成文件与目录树仍可读（后端放开 view 级列表），编辑入口隐藏。

---

## 5. 交互设计

### 5.1 入口

| 入口 | 行为 | 阶段 |
|------|------|------|
| 桌面 Dock | 当前专家空间显示 `FolderOpen`「文件」图标，放在 Dock 最左侧并用分隔线与应用图标隔开；点击打开/聚焦该专家的文件窗口 | P0 |
| 聊天窗标题栏 | 新增「文件」按钮，打开文件窗口并默认「生成文件」、带上当前 `threadId` | P0 |
| 聊天文件卡片 | 预览按钮 → 文件窗口选中该文件；HTML 直接渲染 | P0 |
| 命令面板 | 动作「打开文件」；P1 可搜索产物文件名 | P0 / P1 |
| 任务中心 / 动态 | 打开窗口列表里出现「文件 · 专家名」 | P0（注册表自动继承） |
| 生成通知 | 新产物生成时发桌面通知，点击直达文件 | P2 |

### 5.2 文件窗口布局

```
┌ 文件 · 数据分析专家 ────────────────────────────────┐
│ [生成文件] [全部文件]                [刷新] [下载] │
├──────────────────┬───────────────────────────────────┤
│ 本次会话          │  outbound/销售周报.html            │
│  ▸ 销售周报.html  │  ┌─────────────────────────────┐  │
│  ▸ chart.png     │  │                             │  │
│ 最近会话          │  │     渲染后的 HTML 预览       │  │
│  ▸ 竞品分析.pdf   │  │                             │  │
│                  │  └─────────────────────────────┘  │
│                  │  [预览] [源码]  1.2 MB · 10:32     │
└──────────────────┴───────────────────────────────────┘
```

- 左侧列表宽度可拖拽（复用 `useHorizontalResize`）；窗口可最大化。
- HTML / Markdown 默认渲染，可切源码；其他文本显示源码；图片/视频/PDF/docx 走 `FileViewer`。
- 预览区支持「专注预览」：隐藏左侧列表，让报告占满窗口（不新增窗口类型）。
- 底部状态栏：路径、大小、修改时间（时间统一用 `useServerTimezone`）。

### 5.3 「生成文件」列表

- 数据：该专家最近会话的 `threads.artifacts`，按会话分组，新会话在前；
  同会话内后写入的在前。
- 每项：类型图标、文件名、相对路径、会话标题/时间、生产者徽标（团队/子代理时才显示）。
- 空状态：`还没有生成文件` + 「浏览全部文件」+ 一句引导（“让专家写一份报告试试”）。
- 文件已被删除：打开时 404 → 行内提示「文件已不存在」并提供「移除此项」；不自动改后端账本。
- 刷新：打开时、窗口重新获得焦点时、聊天回合结束后（见 §6.4）。
- 生产者不同：团队会话里产物可能来自其他 agent；打开时用 `item.agent_id ?? 窗口 agentId`，
  与 Chat 页的 `panelFilePaths` 行为一致。

### 5.4 「全部文件」列表

- 懒加载目录树（根 → 子目录），显示文件大小/修改时间；默认展开根。
- 只读：不显示上传/删除/移动/编辑，也不提供「完整工作区」抽屉；保留下载、预览、复制路径。
- 权限：目录树沿用现有的 **owner-only** 语义（`require_running_workspace(..., owner_only=True)`）。
  非 owner 的共享查看者只显示「生成文件」Tab（其中的路径来自其在聊天中已可见的产物），
  不显示「全部文件」；若后端返回 403，显示无权限空态而不是报错弹窗。
- 系统目录：不主动隐藏 `.octop`（保持与现有工作区一致），但预览 token 明确拒绝（见 §7.3）。
- 空目录 / 后端不支持：沿用现有错误文案与重试。
- 专家停止：仍可列出（P0 后端小改），顶部显示只读徽标。

### 5.5 HTML 浏览

- 默认渲染；工具栏提供「源码」切换（源码走现有 `/workspace/file`，带 JWT，不经过 preview token）。
- 相对资源（CSS/JS/图片/字体）通过 preview 能力 URL 加载；自包含 HTML 也走同一路径。
- 外部资源默认被 CSP 拦截（CDN 脚本、外链图片）；P1 在预览内注入 CSP 违规上报，
  顶部显示「已拦截 N 个外部资源」的提示，并提供「下载后在浏览器打开」。
- 外链点击：先在 iframe 内导航（被沙箱限制，不影响控制台）；P1 通过 postMessage
  交给桌面，用现有 `external` 窗口打开。
- 文件不在工作区内（例如工具写到 `/tmp/x.html`）：不走 preview token，
  回退 `srcDoc` 或提示下载。
- **默认独立窗口**：从聊天文件卡片、生成文件列表、工作区树点击 `.html`/`.htm` 时，
  打开或聚焦该文件自己的 `html` 窗口；文件窗口显示「已在独立窗口打开」，并提供
  「在此预览」切换。每个 HTML 页面独立一个窗口，可并排查看图表/打卡页面。

### 5.6 与聊天的接线

- `DesktopChatWindow` 内包一层 `ChatFilePreviewProvider`：
  - `openFilePreview(path, agentId)` → 回调 `onOpenFile({ agentId, path, threadId })`；
  - `openKnowledgeCitation` 在桌面暂不提供，可置为打开文件或 no-op（不阻塞）。
- 效果：`MessageFileCard`、`ToolMediaStrip`、`ChatMediaPlayer` 里的文件卡片自动出现预览按钮。
- 聊天标题栏「文件」按钮：`onOpenFiles(agentId, threadId)`。
- 保留现有下载行为不变。

### 5.7 桌面浏览器窗口

- 每个专家 Dock 固定「浏览器」入口，打开一个空白浏览器窗口；地址栏输入 http(s) URL。
- 同一专家复用一个窗口；当前 URL 持久化到窗口状态，刷新页面后恢复。
- iframe `sandbox="allow-scripts allow-same-origin allow-forms allow-modals"`。
  `allow-same-origin` 是必须的：没有它文档是 opaque origin，ES module 脚本会被 CORS
  拒绝，`localStorage` 抛 `SecurityError`——实测打开 `https://cc.7zhilu.com:8088`
  会卡在 Loading。加上后页面正常加载并可登录。
- 外站仍受浏览器同源策略隔离；与控制台同源的页面会共享控制台 origin，这是
  「在桌面内打开控制台自身」的有意取舍。
- 禁止 iframe 嵌入的站点仍可能显示空白/浏览器错误；工具栏保留「在系统浏览器打开」。

### 5.8 桌面对话窗口：思考过程与生成文件

- **思考过程**：桌面聊天不再使用 `compactProcess`，复用 Chat 的
  `AssistantProcessSummary`——思考/工具过程以可折叠摘要显示在对话流内，
  默认折叠、点击展开；不弹出抽屉，不逃逸窗口。
- **生成文件**：给 `MessageList` 传入 `onEditFile`，回合内出现「编辑了 N 个文件」
  入口，点击打开/聚焦该专家的桌面文件窗口（生成文件 Tab）。文件窗口里的 HTML
  再默认打开独立 HTML 窗口；`MessageFileCard` 的预览按钮也走同一路径。
- 不新增顶层 Drawer / Modal：所有文件预览和完整内容都落在桌面窗口容器内。

### 5.9 专家停止 / 离线

- 文件窗口是只读视图，不依赖 harness 会话；后端把 `workspace/tree` 从
  `require_running_workspace` 放宽到 `require_agent_workspace`
  （保持 owner-only，只放宽「必须运行中」）。
- 文件读取/下载本来就支持停止状态（`workspace.py:166`、`:346`）。
- 发送类操作仍要求运行中；文件窗口不提供发送。

### 5.10 对话内 AppCard「在侧栏打开」

- AppCard 的 `host.openSidePanel` 在桌面模式下不落到 Chat 页的全局 dock，而是由
  `ToolHostScopeProvider`（React context）接管，在**当前桌面对话窗口内部**右侧
  打开面板；多个专家会话窗口各自独立，不互相串台。
- 面板内 `app_card` 直接渲染 `MiniAppFrame`，不经过 AppCard 的 `docked` 分支：
  `composeMessage` 只预填本窗口输入框，不会 `navigate('/chat/…')` 离开 Agent OS
  桌面；`openExternal` 经 `DesktopGrid.openExternalWindow` 打开桌面内 web 窗口。
- 不使用顶层 Drawer / Portal / Modal：面板是窗口内 flex 子项，MiniApp 仍运行在
  `sandbox="allow-scripts"` iframe 中。
- 同一面板也承载外链游戏；关闭面板、切换/新建会话或关闭窗口即清理。

---

## 6. 前端设计

### 6.1 窗口注册表扩展

```ts
// desktopWindows.ts
export type DesktopWindowKind = "app" | "chat" | "external" | "files";

export interface DesktopWindowState {
  // …existing
  /** files window: workspace path to select on open. */
  path?: string;
  /** files window: thread whose artifacts should be shown first. */
  threadId?: string;
  /** files window: active tab. */
  filesTab?: "generated" | "all";
}

// registry
files: {
  kind: "files",
  labelKey: "apps.desktop.files.label",
  labelFallback: "Files",
  icon: FolderOpen,
  minWidth: 720,
  minHeight: 480,
  identity: ({ agentId }) => (agentId ? `files:${agentId}` : null),
  isValid: (win) => Boolean(win.agentId),
}
```

配套改动：

- `loadDesktopWindows()` 的 kind 白名单改为 `kind in WINDOW_REGISTRY`；
  `path` / `threadId` / `filesTab` 做类型清洗后恢复。
- `windowSpaceId()`：`files` 归 `win.agentId`（与 chat 相同）。
- `desktopWindowTitle()`：`files` 返回 `文件 · {专家名}`（标题由 DesktopGrid 结合 spaces 生成）。
- `DesktopGrid` 渲染链在「chat」之前加显式分支：
  `win.kind === "files" && win.agentId ? <DesktopFilesWindow … /> : …`，
  避免落到最后的 `win.agentId ? <DesktopChatWindow>`。
- Dock：专家空间追加「文件」项（`kind: "files"`），`onActivate` 打开/聚焦。
- 命令面板：`actions` 组增加「打开文件」；`window` 组自动包含 files 窗口。
- 访问控制：与 chat 相同，`knownAgentIds.has(agentId) && !accessibleAgentIds.has(agentId)` 时拒绝打开。

**一个专家一个文件窗口**（`files:<agentId>`），再次打开只聚焦并更新 `path` / `threadId`，
类似 Finder 的单窗口语义。

### 6.2 组件结构

```
DesktopFilesWindow
├─ FilesToolbar        Segmented(生成文件/全部文件) · 刷新 · 下载
├─ GeneratedFilesList  产物分组列表（useAgentArtifacts）
├─ WorkspaceTree       懒加载目录树（useWorkspaceTree）
└─ FileViewer          只读预览（复用现有组件）
   └─ HtmlPreviewFrame  iframe src=能力 URL；失败回退 srcDoc
```

**复用策略**

- `FileViewer` 已是统一渲染入口，直接复用；新增只读模式（`editMode=false` 已有）。
- `FilePreview` 增加可选 `src?: string`：有 `src` 用 `iframe src`，否则维持 `srcDoc`。
  `FileViewer` 透传 `htmlSrc`。WorkspaceDrawer 可后续切换到同一能力 URL。
- 目录树：优先从 `WorkspaceDrawer` 抽出 `useWorkspaceTree`（根加载、懒加载子目录、
  刷新、排序、错误提示），桌面窗口与抽屉共用；抽屉的改名/移动/删除逻辑留在原组件。
  若抽取代价过大，退路是给 `WorkspaceDrawer` 增加 `readOnly` + `initialPath` 后
  以 `embedded` 方式放进文件窗口。
- 不复用 `WorkspaceDrawer` 的工具栏做桌面主 UI：它的抽屉语义、写操作和移动端布局
  与桌面窗口不一致，容易让只读目标变形。

### 6.3 状态与持久化

| 状态 | 位置 | 说明 |
|------|------|------|
| 窗口 `path` / `threadId` / `filesTab` | `DesktopWindowState`（localStorage） | 刷新页面后恢复上次选中文件；文件不存在时显示缺失态 |
| 树的展开/选中 | 文件窗口本地 state | 不持久化，保持轻量 |
| 文件内容 | 选中时按需加载 | 不预取所有文件 |
| preview token | 组件内 ref/state | 不写入 localStorage；过期重新申请 |
| 产物列表 | 组件 state + 刷新事件 | 见 §6.4 |

### 6.4 刷新策略

- 打开时加载。
- 窗口重新可见/获得焦点时刷新产物列表（轻量，一次请求）。
- **聊天回合结束**：`DesktopChatWindow` 在 `useChat` 的 `artifacts` 变化或 `streamEnd`
  时派发 `window.dispatchEvent(new CustomEvent("octop:desktop-artifacts", {detail:{agentId, threadId}}))`；
  文件窗口监听同 agent 的事件后刷新产物列表。若当前选中的文件仍在列表里，保留选中。
- 不做高频轮询；P1 如需可加 10s 的可见性轮询兜底。
- 树不自动刷新（避免目录变化打断浏览），由「刷新」按钮触发。

### 6.5 i18n

新增 key 组 `apps.desktop.files.*`（中/英同步），包括：

```
label / generated / all / refresh / openWorkspace / emptyGenerated /
offlineBadge / missingFile / source / preview / download / copyPath /
producer / thread / blockedResources / openInBrowser
```

窗口注册表的 `labelKey` 与任务中心、Dock、命令面板共用同一 key。

---

## 7. 后端设计

### 7.1 复用清单

| 需求 | 现有接口 |
|------|----------|
| 目录列表 | `GET /api/agents/{id}/workspace/tree?path=…&from_workspace=true` |
| 读文本 | `GET /api/agents/{id}/workspace/file?path=…` |
| 下载 | `GET /api/agents/{id}/workspace/download?path=…` |
| 媒体预览 | `GET /api/agents/{id}/media/preview?source=…` |
| 文档转 Markdown | `GET /api/agents/{id}/workspace/doc?path=…` |
| 会话产物 | `GET /api/agents/{id}/threads` 已返回 `artifact_refs` |

### 7.2 新增 A：产物列表接口（P0，推荐）

```http
GET /api/agents/{agent_id}/artifacts?limit=50&thread_id=<optional>
Authorization: Bearer <jwt>
```

```json
{
  "items": [
    {
      "path": "outbound/销售周报.html",
      "name": "销售周报.html",
      "agent_id": "agt_…",
      "thread_id": "thr_…",
      "thread_title": "周报整理",
      "last_active": 1763000000,
      "in_workspace": true
    }
  ]
}
```

- 鉴权：`require_agent_row`（view 级；共享查看者可用），`as_user` 语义与线程列表一致。
- 实现：`thread_registry.list_threads(agent_id, user_id, limit=…)` → 逐线程
  `thread_artifacts_payload(r.artifacts, workspace_dir, default_agent_id=agent_id)` →
  展平、按 `(agent_id, path)` 去重、按会话 `last_active` 倒序。
- 路径归一化：能落在 `BackendWorkspace.workspace_dir` 内的宿主绝对路径转成
  工作区相对路径并置 `in_workspace: true`；工作区外的工具输出保留原样并置
  `in_workspace: false`（前端用 `from_workspace=false` 读取/下载，但不提供能力 URL 预览）。
- 纯函数放在 `infra/agents/threads/artifact.py`（`collect_recent_artifacts`），
  路由只做参数与响应映射，便于单测。
- 不检查文件是否存在（远程后端逐文件 stat 太贵）；UI 打开时 404 显示缺失态。
- 若想进一步缩减 P0 范围：可先用现有 `GET /threads?limit=20` 在**前端**展平，
  接口以后再加。推荐直接加接口，避免文件窗口依赖聊天会话的完整 payload。

### 7.3 新增 B：HTML 预览能力 URL（P0 目标）

#### 7.3.1 Token 模块

新增 `src/octop/infra/agents/workspace/preview_tokens.py`，形态对齐
`infra/apps/game_sessions.py`：

```python
WORKSPACE_PREVIEW_TOKEN_TYPE = "workspace_preview"
DEFAULT_PREVIEW_TTL = 10 * 60  # 10 分钟

create_workspace_preview_token(secret, *, agent_id, user_id, root, as_user=None,
                               is_admin=False, ttl_seconds=…) -> (token, expires_at)
decode_workspace_preview_token(secret, token) -> dict
# claims: typ, agent_id, user_id, root, as_user, admin, iat, exp
```

关键点：

- **不要带 `sub` claim**。`resolve_user_from_token` 只认 `sub`，带 `sub` 的预览 token
  会被当成完整 access token（`api/deps.py:117`）。这里用 `user_id`。
- 同一个 `secret_repo.get("jwt")`，独立 `typ`；没有新密钥，也没有 DB 变更。
- `root` 是**工作区相对目录**（token 授权范围），不是整个工作区。
- 不引入服务端会话表；TTL 到期自然失效。

#### 7.3.2 申请接口

```http
POST /api/agents/{agent_id}/workspace/preview-session
Authorization: Bearer <jwt>

{ "path": "outbound/销售周报.html" }
```

```json
{
  "token": "…",
  "base_path": "/workspace-preview/<token>/",
  "entry": "销售周报.html",
  "expires_at": 1763000600
}
```

- 鉴权：`current_user` + `require_agent_row`（view 级）；`as_user` 写入 token。
- 校验：
  - `path` 必须是工作区相对路径；拒绝绝对路径、`..`、反斜杠、盘符、NUL。
  - 拒绝任意路径段的 `.` 前缀（`.octop`、`.env`、`.git` 等）与 `_builtin_skills`。
  - 只允许 `.html` / `.htm` 文件或目录；目录用于 `index.html` 场景。
  - 用 `BackendWorkspace` 确认存在（`als` / `adownload_bytes`），**不直接读宿主路径**。
  - `root = dirname(path)`（文件）或 `path`（目录）；`entry` 是 `path` 相对 `root` 的片段，
    directory 场景优先取 `index.html`（若存在）否则为空。
- 响应 `base_path` 是 API 相对路径；前端用现有 `getApiUrl()` 拼接 `base_path + encodeURIComponent(entry)`，
  以兼容 `BASE_URL` 指向远端 API 的桌面壳。

#### 7.3.3 服务路由

```http
GET /api/workspace-preview/{token}/{path:path}
```

- 挂载：新 router 或 `workspace.py` 内的独立 `preview_router`，prefix `/api`。
- **JWT 豁免**：在 `api/deps.py:32` 的 `_JWT_EXEMPT_PREFIXES` 增加
  `"/api/workspace-preview/"`，注释与 game-sessions 一致：
  “scoped, short-lived preview token is the credential”。路由内部自行验 token。
- 验 token：
  - `typ == "workspace_preview"`；过期 → `TOKEN_EXPIRED`；非法 → `AUTH_FAILED`。
  - 取 `user_id` → `user_manager.get_by_id`；重新执行 `require_agent_row`
    （共享被撤销时立即失效），带 token 里的 `as_user`。
  - `workspace_for_agent(agent_id)`：停止状态也能拿到 workspace（`manager.py:1345`）。
- 路径：
  - 拒绝空段、`.`/`..`、绝对路径、反斜杠、盘符、NUL、任意 `.` 前缀段、`_builtin_skills`；
  - `rel = posixpath.join(root, *parts)`，交给 `ws.adownload_bytes(rel)`。
    相对路径会走 `BackendWorkspace.resolve_path` 的工作区包含检查（越界抛 `PermissionError`）。
  - 工作区外的宿主绝对路径一律不支持预览（可走 srcdoc / 下载）。
- 响应头（HTML）：
  ```
  Content-Type: text/html; charset=utf-8
  Content-Disposition: inline
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
  Cache-Control: no-store
  Content-Security-Policy: sandbox allow-scripts allow-forms;
    default-src 'none';
    script-src 'unsafe-inline' <host>;
    style-src 'unsafe-inline' <host>;
    img-src <host> data: blob:;
    font-src <host> data:;
    media-src <host> data: blob:;
    connect-src 'none'; worker-src 'none'; frame-src 'none';
    object-src 'none'; base-uri 'none'; form-action 'none'
  ```
  - `<host>` 取自请求 `Host` 头，用严格正则校验（字母/数字/点/连字符/端口/IPv6 括号）；
    校验失败则去掉 `<host>`，退化为只允许内联 + data/blob（自包含 HTML 仍可用）。
  - 不用 `'self'`：沙箱文档是 opaque origin，`'self'` 不可靠；显式 host 才能让相对资源加载。
  - 无 scheme 的 host-source 同时匹配 http/https，兼容反向代理。
- 非 HTML 资源：
  - 按扩展名给 MIME（html/css/js/mjs/svg/png/jpg/woff2/…）；
  - 未知类型 → `application/octet-stream` + `Content-Disposition: attachment`；
  - SVG 等可作为文档直接访问的类型附加 `Content-Security-Policy: sandbox`；
  - 所有响应 `nosniff` + `no-store`。
- 错误映射：`TOKEN_EXPIRED` / `AUTH_FAILED` / `FORBIDDEN` / `NOT_FOUND` /
  `WORKSPACE_OP_UNSUPPORTED`，全部已有 i18n 文案。

> 已用本地 Chromium 做过行为验证（2026-11-16）：在该 CSP + `sandbox="allow-scripts"` 下，
> 相对 `style.css` / `app.js` / `pic.svg` 均正常加载；父窗口 `document` 与 `localStorage`
> 抛 `SecurityError`；外部 `fetch("https://example.com")` 被 `connect-src 'none'` 拦截。
> 实现时应把这一场景固化为前端测试或 e2e 用例，而不是依赖文档。

#### 7.3.4 前端 iframe

```tsx
<iframe
  title="HTML preview"
  src={getApiUrl(`${basePath}${encodeURIComponent(entry)}`)}
  sandbox="allow-scripts allow-forms"
  referrerPolicy="no-referrer"
  className={styles.desktopFilesHtmlFrame}
/>
```

- 不再需要 `allow-same-origin`；没有它，页面无法读控制台 localStorage / cookies / 父 DOM。
- 文档 URL 本身就是 `…/<token>/report.html`，相对资源自然解析到同目录；
  不需要注入 `<base>`（`base-uri 'none'` 也允许我们这么做）。
- token 过期：重新申请并换 `key` 重载 iframe；P0 在「刷新」时续签即可。

#### 7.3.5 为什么不直接把 JWT 放进 iframe URL

现有 API 支持 `?access_token=`，但把它放进 iframe 有三点问题：

1. 预览页面能读到自己的 URL，等于把**完整控制台凭据**交给 Agent 生成的页面；
2. 相对资源的请求不会携带 query，图片/CSS 仍然 404；
3. CORS / 代理日志 / Referer 都可能泄露长期 JWT。

preview token 是只读、单专家、单目录、10 分钟的能力凭据，泄露半径远小于 JWT。

### 7.4 小改：停止专家也能列目录

把 `workspace/tree` 的 `require_running_workspace` 改为 `require_agent_workspace`
（`owner_only=True` 保持不变，**不改变 ACL 范围**，只放宽「必须运行中」）。
`workspace_for_agent` 在停止时会从 agent row 重建 backend（`manager.py:1345`），
读取/下载已经采用同样的语义。

- 风险：目录列表依赖的 backend 配置在运行期被重建，停止状态下可能与运行中略有差异；
  在单测里覆盖 local backend 的停止场景即可。
- 不改变写操作（上传/删除/移动/写文件）仍要求运行中。
- 新增的产物接口与 preview token 使用 **view 级**（`require_agent_row`），
  与「已知路径可读」的现有 `read_file` / `download` 一致；不扩大到目录枚举。

### 7.5 不新增 DB 迁移 / OpenAPI 要求

- 无新表、无新列；`tests/unit/db/test_db_pool.py` 的版本断言不变。
- 新路由补齐 `summary` / `description` / `response_model`；在 `/api/docs` 下可读。
- 新错误文案复用现有 `ErrorCode`，不新增 `errors.*` key；前端 `apiErrors.*` 无需改。

---

## 8. 安全模型

### 8.1 威胁与控制

| 威胁 | 控制 |
|------|------|
| 生成 HTML 读取控制台 JWT / localStorage / 父 DOM | iframe `sandbox="allow-scripts allow-forms"`（无 `allow-same-origin`）+ 响应头 CSP `sandbox`；opaque origin |
| 生成 HTML 把工作区数据外传 | `connect-src 'none'`；`script-src` / `style-src` 只允许预览 host；`img-src` / `media-src` 只允许预览 host + `data:` / `blob:`；`font-src` 只允许 host + `data:`；`frame-src 'none'`；无外部 `<img>` 信标 |
| preview token 泄露 | 只读、单专家、单目录、10 分钟；不带 `sub`；服务时重新鉴权；`Referrer-Policy: no-referrer` |
| 路径穿越 / 越权读 | 拒绝 `..`、绝对路径、反斜杠、盘符、NUL；`BackendWorkspace.resolve_path` 工作区包含；拒绝 `.octop` / `.env` / `.git` / `_builtin_skills` |
| `Host` 头注入 CSP | 严格正则校验；失败则退化为不允许 host 的 CSP |
| 直接打开预览 URL | 响应头 CSP `sandbox` 直接隔离；不依赖 iframe 属性 |
| 权限撤销后 token 仍可读 | 每次资源请求重新执行 `require_agent_row` |
| 生成 HTML 触发弹窗/顶层导航 | 不授予 `allow-popups` / `allow-top-navigation`；`form-action 'none'` |
| 大文件一次性读入内存 | P1 增加大小上限与 `413`；P0 沿用现有下载路径的边界 |

### 8.2 残余风险（需要接受）

- **脚本是允许的**：交互式报告需要 JS；沙箱保证它只能操作自己的文档。
- **报告本身可能包含敏感工作区数据**：这是 Agent 产出的内容，按用户内容对待；
  不做服务端 HTML 净化（净化会破坏报告）。
- **外部 CDN 资源会被拦截**：依赖外部脚本的报告可能显示不全；用提示 + 下载兜底，
  而不是放开 `script-src *`。P1 可加「允许外部资源」的显式开关（默认关）。
- **外链会在 iframe 内导航**：不影响控制台；P1 用 postMessage 交给 `external` 窗口。
- **脚本可以让 iframe 自身导航到外部 URL**：`connect-src 'none'` 阻止 fetch/XHR/beacon，
  但浏览器没有可用的 `navigate-to` 约束；恶意报告理论上可把数据放进外部 URL 的查询串。
  这是「允许脚本的 HTML 预览」的固有残余风险（MiniApp 沙箱同样存在）。P1 可增加
  「安全模式（禁用脚本）」或依赖部署侧网络出口策略。
- **共享查看者的可见范围**：目录树保持 owner-only；共享查看者能看到自己会话里的产物，
  并打开聊天中已出现的文件路径（与现有 `read_file` 的 view 级权限一致）。
  如果产品要求「共享者完全看不到工作区文件」，应另做一次权限收窄，不在本设计内静默改变。
- **浏览器 / external 窗口的 `allow-same-origin`**：这是通用网页窗口的必需能力（模块脚本、
  Cookie、localStorage）。不同源的站点仍被同源策略隔离；若页面与控制台同源，它可以访问
  控制台 origin 的存储。桌面浏览器面向用户主动输入，external 窗口面向 MiniApp 外链，
  两者都按「可加载现代站点」优先处理；若需要更严格的隔离，应改用独立预览 origin。
- **根目录报告的 token 作用域较大**：preview token 的 root 是 HTML 所在目录；
  若报告位于工作区根，token 在 10 分钟内可读工作区根下的非点文件（`.octop` / `.env`
  等仍被拒绝）。这是能力 URL 的固有边界；如不能接受，可在 P1 增加「仅允许
  `outbound/` 或报告子目录」的产品约束，或把报告统一生成到子目录。

---

## 9. 数据与刷新

| 问题 | 设计 |
|------|------|
| 生成文件的权威来源 | `threads.artifacts`（工具产出的工作区路径 + 生产者 agent） |
| 如何排序 | 会话 `last_active` 倒序；同会话内按产物追加顺序倒序 |
| 去重 | `(agent_id, path)`；路径大小写按平台处理（复用 artifacts 的 dedupe 逻辑） |
| 文件被删/移走 | 列表保留，打开时按 404 显示「文件已不存在」；刷新后若后端返回新列表则自然消失 |
| 跨后端的路径 | 产物路径可能是宿主绝对路径；读取走现有 `from_workspace=false` 语义；预览 token 只接受工作区内路径 |
| 预览 token 生命周期 | 10 分钟；打开/刷新/重新聚焦时续签；不落 localStorage |
| 目录树缓存 | 组件内缓存，刷新按钮/重新打开时重置 |
| 多用户共享 | 产物列表按当前用户的会话；全部文件按 agent 访问权限展示 |

---

### 9.1 性能约束（新功能）

- **桌面窗口 memo 化**：`DesktopChatWindow / DesktopFilesWindow / DesktopHtmlWindow /
  DesktopBrowserWindow / DesktopWebView` 使用 `React.memo`；`DesktopGrid` 在拖拽/缩放时
  每帧更新 preview state，不能因此让所有聊天和 iframe 子树重渲染。相关回调
  （`onOpenFiles`、`onSeedConsumed`、`onNavigate`、`onOpenHtml`）保持稳定引用。
- **产物刷新合并**：聊天每次文件工具完成都会派发刷新事件；文件窗口 800ms 防抖，
  且只在「生成文件」Tab 且页面可见时请求，避免一轮多工具调用打出一串 HTTP。
- **文件预览 Context 稳定**：`ChatFilePreviewProvider` 的 value 用 `useMemo`，
  避免流式 token 更新时所有文件卡片重渲染。
- **窗口生命周期（资源上限）**：最小化窗口 5 秒后卸载 iframe；切到其他桌面的
  browser / HTML / external 窗口 60 秒后卸载，普通 MiniApp 5 分钟后卸载。
  恢复时重新加载页面。MiniApp 轮询在窗口隐藏时立即停止。
- **恢复不预加载**：刷新/重新进入桌面时，只有当前桌面里可见的窗口会挂载；
  其他桌面、最小化的窗口保持休眠，直到用户第一次切过去（避免一次恢复几十个
  iframe / 聊天历史）。
- **MiniApp 轮询分级**：前台（当前桌面最上层）窗口 5 秒轮询 `data_version`，
  可见但非前台窗口 30 秒，隐藏窗口不轮询；baseline 跨启停保留，重新激活时
  仍能收到停轮询期间的变化。多窗口并排时只有最上层保持高频刷新。
- **存活 iframe 预算**：每个桌面最多 6 个成本单位（MiniApp/HTML=1，
  网页窗口=2），按窗口 `z`（聚焦时间）保留最近使用的窗口；超出的可见窗口
  显示休眠占位，点击/置顶/从 Dock 激活时唤醒并挤掉最久未用的窗口。
  预算只影响 iframe 类窗口；聊天窗口不计入。该状态是渲染期派生值，不持久化。
- **控制台自嵌套保护**：在同源窗口里打开 Octop 控制台本身会再跑一整套 SPA
  （`/apps/desktop` 还会恢复第二套桌面窗口）。浏览器/external 窗口默认显示
  资源提示，用户可显式「仍在此窗口加载」；API 路径（`/api/*`）不拦截。
- **iframe 本身的成本**：每个 MiniApp 窗口是 wrapper + app 两层 iframe，网页窗口
  是一层完整文档；HTML 页面里的复杂图表/动画也会持续占用 CPU。隐藏窗口卸载后
  这部分成本才释放。后端新增接口没有轮询/后台任务，实测服务进程约 200MB RSS、
  CPU 空闲，瓶颈不在后端。

## 10. 分期与验收

### P0 — 可用闭环（建议本次实现）

| 项 | 内容 | 验收 |
|----|------|------|
| 窗口 | `files` kind + 注册表 + 持久化 + 渲染分支 + Dock/命令面板入口 | 打开/聚焦/最大化/关闭/刷新后恢复；任务中心可见 |
| 生成文件 | 产物接口 + 分组列表 + 空态/缺失态 | 聊天生成 HTML 后，列表出现且可打开 |
| 全部文件 | 只读懒加载树 + `FileViewer` 预览 | 图片/PDF/Markdown/HTML 均可预览；写操作不可见 |
| HTML | preview token + 预览路由 + iframe（srcdoc 兜底） | 相对图片/CSS 正常；外链/外部脚本被拦截；页面无法访问父窗口 |
| 聊天接线 | `ChatFilePreviewProvider` + 标题栏「文件」+ 产物刷新事件 | 桌面聊天的文件卡片出现预览按钮，点击打开对应文件 |
| 停止专家 | `workspace/tree` 放宽运行状态（ACL 不变） | 停止后仍能浏览/预览，顶部只读提示 |
| 质量 | i18n 中英、OpenAPI 摘要、单测、`make all` | 见 §11 |

**明确不在 P0**：独立 HTML 窗口、外链桥接、产物搜索、文件 mtime、通知、移动端适配。

### P1 — 体验与加固

- ✅ 独立 `html` 窗口（`html:<agentId>:<path>`），支持多份报告并排。
- 预览内 CSP 违规上报 + 「已拦截外部资源」提示。
- 产物列表显示文件存在性/修改时间（按目录批量 stat，远程后端可跳过）。
- 命令面板搜索产物文件名；按会话/类型过滤。
- 外链点击用 postMessage 转交 `external` 窗口。
- 预览大小上限与 `413`；预览 token 指标（`METRICS`）。
- HTML 预览的「安全模式（禁用脚本）」开关。
- WorkspaceDrawer 的 HTML 预览也切到能力 URL（相对资源同样受益）。

### P2 — 生态

- 新产物桌面通知 / 动态中心条目，点击直达文件。
- 移动端：小屏回退到 `WorkspaceDrawer` 抽屉；文件窗口全屏 sheet。
- 「导出为独立 HTML 包」：把相对资源内联，便于离线分享。
- 桌面窗口内按权限显示编辑（仍复用工作区写接口，不新增规则）。
- 可选「允许外部资源」开关（按专家或全局，默认关）。

---

## 11. 测试与验收

### 11.1 后端

| 测试 | 覆盖 |
|------|------|
| `tests/unit/agents/test_preview_tokens.py` | 签发/解析、过期、非法 typ、无 `sub` 不能被当 access token |
| `tests/unit/api/test_workspace_preview.py` | 路径穿越（`..`、URL 编码、反斜杠、盘符）、`.octop`/`.env`/`_builtin_skills` 拒绝、缺失文件、停止专家 |
| `tests/unit/api/test_agent_artifacts.py` | 去重、排序、跨会话、生产者 agent、limit |
| `tests/integration/test_workspace_preview_api.py` | 申请 token → 取 HTML（CSP/nosniff/no-store）→ 取相对图片；共享撤销后 403；过期 token |
| `tests/unit/db/test_db_pool.py` | 版本断言不变（无迁移） |

### 11.2 前端

| 测试 | 覆盖 |
|------|------|
| `desktopWindows.test.ts` | `files` 的 identity / 最小尺寸 / dock inset / 持久化 round-trip（含坏数据丢弃） |
| `DesktopFilesWindow.test.tsx` | 生成文件分组、空态、缺失态、树选择、HTML iframe `sandbox` 属性与 src、源码切换 |
| `DesktopChatWindow.test.tsx` | 包 Provider 后文件卡片出现预览按钮并回调 `onOpenFile` |
| `DesktopGrid.test.tsx` | Dock/命令面板出现「文件」；渲染分支不落到聊天窗；访问控制拒绝 |
| `FilePreview.test.tsx` | `src` 优先于 `srcDoc`；无 `src` 时行为不变 |

### 11.3 手工安全清单

1. 生成一个包含 `fetch("https://example.com")`、外链图片、内联脚本的 HTML：
   内联脚本可运行；外链请求被 CSP 拦截（DevTools Console 有 violation）。
2. 在预览页面 Console 执行 `parent.document` / `localStorage`：被拒绝（opaque origin）。
3. 预览 URL 直接在新标签打开：页面仍受 `sandbox` CSP 限制，无法读取控制台凭据。
4. 构造 `../.env`、`%2e%2e%2f`、`..\\`、`C:\\` 请求：全部 4xx，不返回内容。
5. 共享用户被移除后，旧 preview URL 立即 403。
6. 专家停止后打开文件窗口：树与预览正常，写操作入口消失。

### 11.4 交付命令

```bash
uv run pytest tests/unit -x -q
uv run pytest -m "not live"
cd dashboard && npx tsc -b
cd dashboard && ./node_modules/.bin/vitest run
make all
make build-frontend   # 发布前
```

（Windows CI 同样必须绿；路径测试使用 `tmp_path` / `Path` 语义，不写死 POSIX 路径。）

---

## 12. 待确认问题

1. **只读边界**：已确认——桌面文件窗口只读，并移除「完整工作区」抽屉；
   编辑/上传仍使用控制台侧栏的 Agent 工作区页面。
2. **HTML 资源策略**：默认允许内联脚本、禁止外部网络（CDN/外链图片会被拦截），
   是否能覆盖主要报告场景？是否需要 P1 增加按专家的「允许外部资源」开关？
3. **生成文件范围**：默认「最近会话的产物」，第二个 Tab 看整个工作区；是否需要
   「只看当前会话」的显式过滤？
4. **是否要「一键发布为小程序」**：本设计建议不做，保持「文件」与「MiniApp」边界；
   若需要，应作为独立产品能力评审（数据表、能力清单、发布快照都会牵涉）。
5. **独立 HTML 窗口**：已按 P1 实现——每个 HTML 文件独立窗口，默认从聊天/文件列表
   打开；如需改为「默认内嵌、手动独立」，只需调整 `DesktopFilesWindow` 的默认策略。

---

## 13. 建议改动文件清单

**后端**

- `src/octop/infra/agents/workspace/preview_tokens.py`（新）
- `src/octop/infra/agents/threads/artifact.py`（`collect_recent_artifacts`）
- `src/octop/api/routers/workspace.py`（产物接口、preview-session、tree 放宽）
- `src/octop/api/routers/workspace_preview.py`（新，服务路由）
- `src/octop/api/app.py`（挂载路由）
- `src/octop/api/deps.py`（JWT 豁免前缀）
- `src/octop/api/openapi_meta.py`（如需补充 tag 描述）
- `tests/unit/…`、`tests/integration/…`

**前端**

- `dashboard/src/pages/Apps/desktopWindows.ts`（kind/state/persistence）
- `dashboard/src/pages/Apps/DesktopFilesWindow.tsx` + 样式（新）
- `dashboard/src/pages/Apps/DesktopHtmlView.tsx`、`DesktopHtmlWindow.tsx`（新）
- `dashboard/src/pages/Apps/desktopFiles.ts`（新，共享路径/类型）
- `dashboard/src/pages/Apps/DesktopBrowserWindow.tsx`（新，桌面浏览器窗口）
- `dashboard/src/pages/Apps/DesktopGrid.tsx`（入口/渲染/Provider/刷新事件）
- `dashboard/src/pages/Apps/DesktopChatWindow.tsx`（文件按钮/Provider/threadId）
- `dashboard/src/pages/Agent/Workspace/components/FilePreview.tsx`、`FileViewer.tsx`（`src` 透传）
- `dashboard/src/pages/Agent/Workspace/hooks/useWorkspaceTree.ts`（新，或等价抽取）
- `dashboard/src/api/modules/workspace.ts`（产物/预览 session 调用）
- `dashboard/src/locales/{en,zh}.json`
- 对应 `*.test.ts(x)`
