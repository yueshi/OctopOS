# Agent 桌面小程序组织与管理

> 快照：2026-11-17，分支 `feature/miniapp-capabilities`。
>
> 范围：Agent 桌面（`/apps/desktop`）上的用户 MiniApp 与平台系统应用。工作台看板、公开链接、聊天侧栏不在本文范围内，只在边界处提及。

---

## 0. 结论摘要

1. 当前桌面是「一个专家空间 = 一列扁平图标」，用户 MiniApp 和 5 个系统应用混排；没有分组、隐藏、固定或排序能力。
2. 系统应用是**平台应用**，不属于 Agent 生成物，却按普通 MiniApp 的形态长期占据每个专家桌面的 5 个图标，数量和语义都不合适。
3. 第一批收口：
   - 系统应用收进一个「系统应用」文件夹，桌面只保留一个入口；
   - 新增「管理桌面」抽屉：按专家隐藏系统应用、隐藏用户 MiniApp、固定/取消固定 Dock；
   - Dock 支持用户固定项，未自定义前保留原来的「前 10 个」行为；
   - 组织偏好写入 `localStorage`（键 `octop:apps-desktop-org:v1`），窗口继续沿用现有持久化。
4. 第二批（本次已落地）：
   - 桌面图标拖拽排序（应用与自定义文件夹统一顺序）；
   - 自定义文件夹：创建、重命名、增删应用、删除，应用只能属于一个文件夹；
   - 「全部应用」增加「全部 / 小程序 / 系统应用」筛选与系统应用分区，按专家上下文直接打开对应桌面窗口。
5. 后续再做：服务端同步、桌面右键菜单、系统应用状态徽标、跨专家「控制台」空间。**不**把系统应用塞进 `light_apps`。

---

## 1. 现状模型

### 1.1 空间（Space）

| 空间 | 来源 | 内容 |
|---|---|---|
| 专家桌面 | 每个可访问且启用的专家（自建/共享） | 该专家的用户 MiniApp + 可见系统应用 |
| Shared with me | 无权访问所属专家、但被直接共享的 MiniApp | 用户 MiniApp |
| Unassigned | 没有 `agent_id` 的旧数据 | 用户 MiniApp |
| All Apps | 跨专家全局库 | 用户 MiniApp |

代码：`dashboard/src/pages/Apps/DesktopGrid.tsx` 的 `spaces` / `spaceIdForViewer`。

### 1.2 图标与窗口

- 用户 MiniApp：按名称排序，扁平渲染为 `.desktopIcon`；双击开窗。
- 系统应用：`visibleSystemApps()` 返回 5 个定义（用量、连接、能力、经营看板、任务），和用户 MiniApp 混排；窗口类型 `system`。
- Dock：默认取当前空间前 10 个用户 MiniApp；会话、文件、浏览器和运行中的系统窗口按需追加；末尾是「全部应用」。
- 命令面板：用户 MiniApp + 当前空间系统应用 + 空间/窗口/动作。
- 窗口布局：`localStorage`（`octop:apps-desktop-windows:v1`），最多 30 个窗口。

### 1.3 管理入口

- 用户 MiniApp：只能双击打开，或去应用详情页管理 HTML/数据/协作/发布。
- 系统应用：只能打开，没有隐藏/排序/说明入口。
- 桌面：没有「管理桌面」、没有重置、没有分组。

---

## 2. 问题清单

| # | 问题 | 影响 |
|---|---|---|
| 1 | 系统应用固定占 5 个图标，且每个专家桌面重复 | 桌面拥挤；与 Agent 生成物的语义混淆 |
| 2 | 没有隐藏/固定/排序 | 用户无法按自己的工作方式组织桌面 |
| 3 | Dock 是「前 10 个」而不是「我固定的」 | 高频应用可能不在 Dock，低频应用占位 |
| 4 | 系统应用不在「全部应用」中 | 跨专家查找平台能力只能靠命令面板 |
| 5 | 没有桌面级管理视图 | 用户不知道桌面图标与 Dock 从哪里来 |
| 6 | 组织偏好与窗口一样只在浏览器本地 | 换设备/换浏览器丢失 |
| 7 | 图标没有分组/文件夹/拖拽 | 应用多时只能横向滚动 |
| 8 | 系统应用没有可发现的状态说明 | 「SYS」标记含义不明确（本次改为文件夹） |

---

## 3. 目标模型

### 3.1 桌面条目

```text
DesktopSpace
  ├─ systemFolder?        ❐ 系统应用（一个文件夹图标，内含 N 个平台应用）
  ├─ userApps[]           用户 MiniApp 图标
  ├─ dockAppIds[]         Dock 固定项（缺省 = 前 10 个）
  └─ windows[]            窗口（持久化）
```

### 3.2 组织偏好（Phase 1，localStorage）

```ts
interface DesktopFolder {
  id: string;
  name: string;
  appIds: string[]; // 一个应用只能属于一个文件夹
}

interface DesktopOrgPrefs {
  hiddenSystem: Record<string, string[]>; // agentId -> 系统应用 key
  hiddenApps: string[];                   // app_id
  pinnedApps?: string[];                  // undefined = 默认前 10 个
  folders?: DesktopFolder[];
  order?: string[];                       // app:<app_id> | folder:<folder_id>
}
```

- `hiddenSystem` 按专家隔离：隐藏「用量」不会影响其他专家桌面。
- `hiddenApps` 是全局隐藏：应用仍可在「全部应用」/命令面板中找到，只是不占桌面。
- `pinnedApps === undefined` 保留旧行为；一旦用户固定/取消固定，Dock 由该数组决定。
- 隐藏用户 MiniApp 时自动从 Dock 固定项和所在文件夹移除；空文件夹随之消失。
- 文件夹全局存储，但只有包含当前桌面应用的文件夹才会显示/出现在管理抽屉；创建/编辑时只能选择当前桌面应用。
- `order` 只控制桌面顶层条目；文件夹内部的条目按创建/编辑时选择的顺序展示。

### 3.3 交互

```text
桌面图标区
  [系统应用文件夹] [自定义文件夹] [用户 MiniApp] [用户 MiniApp] ...
       ├─ click → Popover：系统应用 / 文件夹内小程序网格
       └─ drag → 调整顶层顺序（文件夹作为一个条目）

菜单栏
  [管理桌面] → Drawer
       ├─ 系统应用：显示在桌面（Switch，按专家）
       ├─ 小程序：显示在桌面 / 固定到 Dock（Switch）
       ├─ 文件夹：新建、编辑（名称 + 应用）、删除
       └─ 重置桌面组织

全部应用
  [全部] [小程序] [系统应用]
       ├─ 系统应用按专家卡片展示，点击进入该专家桌面并打开窗口
       └─ 小程序保持原有卡片与打开方式
```

- 「管理桌面」同时出现在命令面板动作里。
- 系统应用仍可通过命令面板直接搜索打开，不需要先开文件夹。
- 拖拽只调整顶层顺序；跨专家移动应用仍不在桌面完成。

---

## 4. 代码落点

| 文件 | 职责 |
|---|---|
| `dashboard/src/pages/Apps/desktopOrg.ts` | 偏好模型、localStorage 读写、布局/排序/文件夹、可见性/Dock 纯函数 |
| `dashboard/src/pages/Apps/desktopOrg.test.ts` | 纯函数测试 |
| `dashboard/src/pages/Apps/SystemAppsFolder.tsx` | 系统应用文件夹 + Popover |
| `dashboard/src/pages/Apps/DesktopAppFolder.tsx` | 自定义文件夹图标 + Popover + 拖拽属性 |
| `dashboard/src/pages/Apps/DesktopManageDrawer.tsx` | 管理抽屉（可见性、Dock、文件夹编辑） |
| `dashboard/src/pages/Apps/DesktopGrid.tsx` | 接入文件夹、拖拽排序、管理入口、Dock 过滤、系统应用深链 |
| `dashboard/src/pages/Apps/AllAppsGallery.tsx` | 全部应用筛选、系统应用分区与专家上下文 |
| `dashboard/src/pages/Apps/index.tsx` | 汇总可访问专家的系统应用并跳转桌面 |
| `dashboard/src/pages/Apps/index.module.less` | 文件夹、拖拽态、抽屉、分区样式 |
| `dashboard/src/locales/{zh,en}.json` | `apps.desktop.*`、`apps.all.*` |

---

## 5. 分阶段计划

### Phase 1（本次已落地）

- [x] 系统应用文件夹（一个图标）
- [x] 管理桌面抽屉（隐藏系统应用/用户 MiniApp、固定 Dock、重置）
- [x] Dock 固定项与旧行为兼容
- [x] 命令面板入口
- [x] 纯函数 + 组件测试

### Phase 2（本次已落地拖拽、文件夹、全部应用筛选）

- [x] 拖拽排序与自定义分组/文件夹（创建、重命名、增删应用、删除）
- [x] 「全部应用」筛选：全部 / 用户 MiniApp / 系统应用；系统应用卡片携带专家上下文并深链打开
- [ ] 组织偏好服务端持久化（新增 per-user desktop prefs 表/API，跨设备同步）
- [ ] 桌面右键菜单：打开、固定到 Dock、隐藏、查看详情/管理
- [ ] 系统应用文件夹内显示状态徽标（异常/待处理数量）

### Phase 3（可选）

- [ ] 「我的桌面」/「控制台」空间：账号级系统应用（全局用量、偏好、Bridge）不再按专家重复
- [ ] 每专家系统应用自定义顺序/显示
- [ ] 桌面布局与组织偏好导入导出/随备份恢复

---

## 6. 明确不做

- 不把系统应用注册成 `light_apps`（见 `docs/miniapp-desktop-system-apps-plan.md` §4.1）。
- 不在本阶段做字段级/应用级「桌面权限」；可见性仍由专家所有权 + 模块权限决定。
- 不做跨专家拖拽移动应用（应用归属专家，移动应走应用交接）。
- 不把「全部应用」变成第二个设置页；管理动作留在桌面抽屉。

---

## 7. 验证

```bash
cd dashboard
./node_modules/.bin/vitest run src/pages/Apps/desktopOrg.test.ts \
  src/pages/Apps/DesktopManageDrawer.test.tsx \
  src/pages/Apps/DesktopGrid.system.test.tsx \
  src/pages/Apps/DesktopGrid.test.tsx \
  src/pages/Apps/AllAppsGallery.test.tsx
NODE_OPTIONS=--max-old-space-size=3072 ./node_modules/.bin/tsc -b
```

手工检查：

1. 专家桌面只看到 1 个「系统应用」文件夹，点开后 5 个系统应用。
2. 管理抽屉隐藏「任务」→ 文件夹内少一个，桌面其他专家不受影响。
3. 隐藏一个用户 MiniApp → 图标消失，但「全部应用」仍可见。
4. 固定一个 MiniApp 到 Dock → Dock 只显示固定项；取消固定后恢复。
5. 管理抽屉新建文件夹（选 2 个小程序）→ 桌面只剩 1 个文件夹图标，点开可见这 2 个应用。
6. 编辑文件夹改名/增删应用 → 桌面和文件夹内容同步更新；删除文件夹 → 应用回到桌面顶层。
7. 拖拽两个图标/文件夹改变顺序 → 刷新页面后顺序保留。
8. 全部应用切换到「系统应用」→ 看到各专家系统应用卡片；点击后进入对应专家桌面并打开该窗口。
9. 重置后恢复默认（系统应用全显示、Dock 前 10 个、无文件夹）。
10. 刷新页面偏好保留。

---

## 8. 开放问题

1. 服务端偏好是否与窗口布局一起做，还是先只同步组织偏好？
2. Dock 固定项是否应该有上限（当前仍受 10 个渲染上限约束）？
3. 系统应用是否需要「全部显示在桌面」的兼容开关（习惯旧布局的用户）？
4. 拖拽是否需要在触屏上提供长按排序（当前仅桌面鼠标拖拽）？
