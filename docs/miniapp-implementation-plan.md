# MiniApp 实施落地计划

> 快照：2026-11-14，分支 `feature/miniapp-capabilities`。
>
> 口径：**当前实现状态以 [MiniApp 功能与架构现状](./miniapp-overview.md) 为准**；本文只整理“接下来怎么落地”，不替代现状文档，也不重复 `miniapp-platform-roadmap.md` 的 R2/R3 需求描述。
>
> 范围：上游合并与迁移、专家可见性、能力清单、权限语义、访问申请、PII、结构减负、数据所有权与内置数据集服务，以及 R2.0–R2.4 与 R3 的执行顺序。

---

## 0. 结论摘要

先完成一次 **R1.5 收敛版本**，再开 R2 功能。收敛版本不新增用户功能，只解决会随 R2 放大、且越晚改越贵的地基问题：

1. **上游迁移与专家可见性**：本分支的迁移编号与上游已发布的 `025_user_mfa` 冲突；专家共享已叠加分组可见性，不能继续用 `is_shared` 布尔值。
2. **能力清单 fail-open**：旧应用/旧备份会动态继承未来新增能力（已冻结）；Agent 不能静默提升高风险能力（已收口）。
3. **权限语义**：协作者的 `edit_structure` 实际是“共同所有者”（已限制管理动作）；`edit_schema` / `manage` 拆分待做。
4. **公开面 PII 与访问申请一致性**：公开链接去掉 PII 标记即暴露（已收紧，解除需重新发布）；访问申请的 app/agent 不匹配、结果通知、HTTP 管理员写审计与原子批准已修复；owner 分裂与唯一 pending 索引已完成（schema v35）。
5. **结构与性能**：`AppService` 6 处手工构造已收敛为工厂；主列表已并发读工作区；沙箱 parity 测试已补；`Detail.tsx` 2,980 行的 Tab 拆分待做。
6. **数据所有权**：MiniApp 只做展示、装配与流程编排，不拥有核心业务数据；大多数小应用使用平台内置数据集服务 `builtin.dataset`，不要求每个小应用部署独立后端。

收敛完成后按以下顺序推进：

```
R2.0 内置数据集服务 → R2.1 外部服务绑定 → R2.2 事件 → R2.3 调度/工作流 → R2.4 公开看板
R3：Bundle v2 → IM 认证链接 → 渠道身份绑定
```

存储继续使用**共享控制面 DB**；每 App SQLite、页面本地存储、Electron 运行时都暂缓，理由见 §1。内置数据集是平台多租户托管的应用服务，不是 MiniApp 内嵌存储。

---

## 1. 决策记录

| # | 决策 | 结论 | 理由 |
|---|---|---|---|
| 1 | 内置数据集/行数据存储 | **继续共享控制面 DB**（SQLite/PostgreSQL），`light_app_rows.values_json` 逻辑分区 | 小应用默认绑定 `builtin.dataset`；单表 2 万行/5 MB；共享 DB 保留事务、级联、PostgreSQL/多实例和跨应用管理能力 |
| 2 | 每 App 一个 SQLite | **暂缓，不作为默认架构** | 与控制面失去事务一致性；多实例/PostgreSQL/远程工作区不兼容；文件、连接、迁移、备份运维成本高。仅在单实例/离线/强隔离版本重新评估 |
| 3 | 每 App SQLite 的替代 | 若目标是查询/索引，优先 PostgreSQL `jsonb` + GIN 或投影列；若目标是可移植，R3 可评估“SQLite 作为 Bundle 导出格式” | 成本远低于线上每 App 文件；Bundle 场景才需要单文件自包含 |
| 4 | 页面本地存储 | **暂缓**；如产品确有需求，只做宿主代理的 `octopApp.storage` 小缓存 | 微信的本地 Storage/文件是缓存不是权威数据；当前沙箱是 opaque origin，页面直连存储会绕过 ACL 和生命周期 |
| 5 | Electron | **Web 优先，不把 MiniApp 运行时搬进 Electron**；只做薄桌面壳时才考虑 | 借鉴 main/renderer、preload/contextBridge、权限/导航处理即可；Electron 打包/签名/更新成本高 |
| 6 | 旧应用能力 | `LEGACY_CAPABILITIES` **冻结为 R1 能力集合**，新增能力必须显式开启 | 冻结后 R2 新增能力不会自动授予旧应用和 v1 备份恢复 |
| 7 | 高风险能力提升 | Agent 工具只允许低风险能力；`ai.generate`/`game.launch`/`external.fetch`/`events.emit`/`schedules.manage` 由 Owner 在 Dashboard 开启或走 HITL | 能力清单约束页面代码，不约束 Agent；Agent 不能静默提升副作用能力 |
| 8 | 协作者 `edit_structure` | 短期把删除/交接/分享/公开链接限制为 owner/admin；中期拆 `edit_schema` 与 `manage` | 现状 `edit_structure` 可删除、发布、转交，实际是共同所有者 |
| 9 | 事件顺序 | 先修 `app.import` 的并发/乱序/`replace`，再做 inbox/outbox | 事件平面是 at-least-once；乱序 `replace` 会覆盖新数据 |
| 10 | 调度 | 抽共享 DB 租约原语；先 `sync_source`/`snapshot_publication`，`run_agent` 延后 | 当前 `claim_source` 的“前推一个间隔”租约在长任务下会重叠 |
| 11 | 发布顺序 | R2.4 公开看板最后；R3 先 Bundle v2，再做 IM 认证链接 | 公开面风险最高；Bundle 是工程问题，认证链接牵涉身份与渠道差异 |
| 12 | 本地缓存失效 | 若做本地缓存，用 `data_version` 做缓存键/ETag | 已通过 `GET /apps/{id}/data-version` 与 tables 暴露；比轮询整表开销更小 |
| 13 | MiniApp 数据所有权 | **MiniApp 只拥有装配、投影、编排和本地状态；核心业务数据归应用服务** | 避免 MiniApp 变成业务数据库，避免与独立应用服务双写/双权威 |
| 14 | 小应用默认后端 | **平台内置数据集服务 `builtin.dataset`**，零部署；`builtin` / `connector` / `external` 统一走 binding | 多数小应用不需要独立服务；内置服务仍是独立的数据服务，只是由平台多租户托管 |
| 15 | 小应用业务逻辑 | **声明式规则 + 最小 workflow**；不提供任意服务端 JS | 覆盖校验、状态机、汇总、通知、审批；复杂逻辑再 promote |
| 16 | 内置数据集退出路径 | 可导出 schema/rows/bindings，**promote 到 `external` 服务** | 防止小应用长大后锁死在内置模型 |
| 17 | 行级范围 | 表级 `row_scope: app \| user`；公开面用表级 `public_visible` | `published` 已取消：公开受众与内部协作者可见性是两条正交轴；部分行公开用独立公开表/投影 |

---

## 2. 评审结论回顾

### 2.1 架构

**成立的部分**

- 控制面 DB 存结构化行、工作区存 `app.html`/assets，边界清晰。
- 行级 CAS、`client_request_id` 幂等、replace 空结果拒绝、PII 读时脱敏、发布快照，都是平台级实现。
- 文档把已落地/未落地分开写，现状可信。

**必须收口的问题**

| 优先级 | 问题 | 位置 |
|---|---|---|
| P0 | 迁移编号与上游已发布 `025_user_mfa` 冲突；旧水位库会跳过上游 022–025 | `src/octop/infra/db/migrate.py`、`src/octop/infra/db/migrations/` |
| P0 | 专家可见性未接上游分组模型；访问申请批准只 `set_shared(True)` | `src/octop/infra/apps/service.py` `is_agent_accessible` / `resolve_access_request` |
| P0/P1 | `LEGACY_CAPABILITIES` 动态全开；Agent 可自行提升高风险能力 | `src/octop/infra/apps/capabilities.py`、`src/octop/infra/apps/tools.py` |
| P1 | 协作者 `edit_structure` 可删除/发布/交接/改分享 | `src/octop/api/routers/apps.py` |
| P1 | 公开面用实时 PII 标记覆盖快照；解除标记即暴露 | `src/octop/api/routers/public_apps.py` |
| P1 | 访问申请 app/agent 不校验、owner 分裂、批准非原子、申请人无通知 | `src/octop/infra/apps/service.py`、`src/octop/infra/db/repos/app_access.py` |
| P1 | HTTP 管理员写操作未写 `miniapp.admin_override` 审计 | `src/octop/api/routers/apps.py` `_row_access` |
| P1 | `list_views_with_stats` 串行读工作区；`AppService` 6 处手工构造 | `src/octop/infra/apps/service.py`、各 router/manager |
| P1 | 沙箱安全逻辑 Python/TS 双实现，无跨语言一致性测试 | `src/octop/infra/apps/sandbox.py`、`dashboard/src/components/MiniAppFrame/` |
| P2 | 数据源租约在长任务下可能重叠 | `src/octop/infra/db/repos/apps.py` `claim_source` |

### 2.2 存储

- 一个 Octop 实例一个控制面 DB；所有 MiniApp 共用 `light_apps` / `light_app_tables` / `light_app_rows`，按 `app_id + table_key` 逻辑分区。
- 行值是 JSON 字符串；Schema 是 DB 里的 `schema_json`（v2 权威）；HTML/assets 在工作区。
- 旧应用 `storage_version=1` 行在 CSV，首次访问懒迁移到 DB。
- 公开链接：HTML/表结构是发布快照，行数据实时读 DB。
- 当前限额：50 应用/用户、12 表/应用、50 列/表、2 万行/表、5 MB/表、单元格 1 万字符。

**结论**：共享 DB 足够支撑当前定位；每 App SQLite 是隔离/可移植优化，不是数据模型升级。

### 2.3 微信小程序本地模型

- Storage：单 key 1 MB、单用户单小程序 10 MB；按用户 + appId + 插件隔离；不跨设备；只有代码包被清理时才清空。
- 文件：临时文件仅当前生命周期；缓存文件 + 用户文件合计 200 MB；`USER_DATA_PATH` 可读写。
- 没有官方本地 SQL/IndexedDB；云开发数据库是服务端文档型 DB，不支持离线数据库。
- 本地 Storage/文件是**缓存**，不是权威数据；官方也提示关键数据不要只放本地。

**对 Octop 的启示**：只借鉴配额、隔离、生命周期；不把业务数据放到页面本地。若要补页面状态，做宿主代理的 `octopApp.storage`，而不是直连 localStorage/IndexedDB。

### 2.4 Electron

- 值得借鉴：main/renderer 分离、preload/contextBridge 最小暴露、IPC 白名单、权限/导航/生命周期显式处理、renderer 进程隔离。
- 当前 MiniApp 已经是“浏览器版 Electron”：宿主 = main、iframe = renderer、`octopApp` = preload、能力清单 = IPC 白名单。
- 最大差距：当前 srcdoc iframe 与 Dashboard 同 renderer process，死循环可卡死页面；Electron 的 `WebContentsView` + `session partition` 可解决，但只适合桌面壳，不是 Web 运行时的替代。
- 不建议：Node integration、`<webview>`、把 MiniApp 运行时搬进 Electron main、让远程页面直接拿 `ipcRenderer`。

### 2.5 目标数据模型：组合层 + 最小状态库

MiniApp 的定位是 **展示 + 流程编排 + 组件/服务装配**，不是业务数据库。目标模型分四层：

| 层 | 存什么 | 权威性 | 生命周期 | 存储形式 |
|---|---|---|---|---|
| **A. 装配清单** | 页面/组件、服务绑定、输入输出映射、动作、工作流引用、能力声明 | **权威配置** | draft → validated → published | JSONB + 版本号 |
| **B. 投影缓存** | 从应用服务查询/聚合出的读模型、列表快照、看板数据 | **非权威，可重建** | TTL / LRU / 事件失效 | JSONB + `expires_at` / `data_version` |
| **C. 编排运行状态** | workflow 定义、run、step、attempt、lease、错误、补偿状态 | **权威过程状态** | 按运行保留期清理 | 关系表，事务 + 行级锁 |
| **D. 用户本地状态** | UI 状态、筛选条件、草稿、偏好 | **非权威，按用户/设备** | TTL / 登出 / 删除 App 清理 | 宿主代理 KV，JSON |

**服务提供者统一为三类**：

| provider | 适用 | 说明 |
|---|---|---|
| `builtin` | 大多数小应用 | 平台内置数据集服务：schema、CRUD、查询、聚合、导入导出、ACL、备份 |
| `connector` | 需要外部数据/工具 | 复用现有连接器/MCP，只读或受控操作 |
| `external` | 复杂业务应用 | 独立应用后端服务，提供 HTTP/RPC 契约 |

小应用默认绑定 `builtin.dataset`，不需要部署任何东西；需要外部系统时再加 `connector`；业务复杂到内置模型撑不住时 promote 到 `external`。

**`builtin.dataset` 的最小契约**：

```text
service: builtin.dataset
operations:
  describe()                       → schema / operations / permissions
  query(table, filter, sort, page) → rows + total
  get(table, key)                  → row
  create(table, values, idempotency_key)
  update(table, key, values, expected_version)
  delete(table, key, expected_version)
  aggregate(table, group_by, metrics, filter)
  import(table, file, mode, mapping)
  export(table, filter)
```

- 每个 App 创建时自动获得一个逻辑实例，`app_id` 就是租户键；仍使用共享控制面 DB 的 `light_app_rows` / `light_app_tables`。
- 它与外部服务走同一套 `light_app_bindings`，页面调用方式一致。
- App 删除时随 App 级联清理；备份、公开快照、审计、配额复用现有机制。
- 表级 `row_scope`：`app`（团队台账，协作者看全部）、`user`（个人台账，普通用户只看自己的行，owner/admin 看全部）。公开面由 `public_visible` 单独控制，不进入 `row_scope`；需要只公开部分行时建独立公开表/投影。
- 小应用逻辑优先用声明式规则：校验、必填、枚举、状态机、计算字段/汇总、触发器（`on_create → emit event / notify / audit`）。跨步骤流程走 workflow；不提供任意服务端 JS。

**选择矩阵**：

| 场景 | 推荐 |
|---|---|
| 个人台账、费用报销、客户跟进、库存盘点 | `builtin.dataset` |
| 看板/报表，数据来自外部系统 | `connector` + 投影缓存 |
| 有审批/状态流转的小应用 | `builtin.dataset` + 声明式规则/workflow |
| 需要调用多个外部服务并聚合 | `builtin.dataset` + `connector` + 投影 |
| 高并发、复杂事务、独立合规 | `external` 独立服务（从内置 promote） |
| 纯展示、无自有数据 | 只绑定 `connector`/`external`，不建数据集 |

**Promote 路径**：

```text
内置数据集（builtin）
  → 导出 schema + rows + 绑定清单
  → 生成 external 服务契约/骨架（或由开发者实现）
  → 切换 binding: builtin.dataset → app-service
  → 保留同一套 UI/装配清单
  → 可选双写/回放一段时间，校验一致后停内置写
```

触发 promote 的条件：单表行数/字节接近内置上限；需要跨表/跨服务强事务；需要自定义 API/复杂算法/外部系统写回；需要独立部署、独立数据库或独立备份策略；外部集成方要求独立 API；性能或合规审计要求。

**对现有代码的演进**：

| 现有 | 演进 |
|---|---|
| `light_app_rows` / `light_app_tables` | 成为 `builtin.dataset` 的存储实现；不再叫“MiniApp 业务库” |
| `app_*` 行工具 | 作为 Agent 操作内置数据集的接口，保留 |
| `light_app_sources` | 作为内置数据集的导入/同步入口，或生成投影 |
| R2 计划的 `light_app_services` | 增加 `provider=builtin|connector|external`；内置服务是默认 provider |
| `AppService` | 拆出 `dataset_service.py`（服务契约）和 `bindings.py`；MiniApp 只依赖绑定 |
| 公开链接 | 读内置数据集/发布投影；不 live 调任意外部服务 |
| 能力清单 | `data.read/write` 约束内置数据集；`external.fetch` 约束 connector/external |
| 每 App SQLite | 不做；需要独立库时 promote 到 external 服务 |

---

## 3. 阶段计划

### Phase 0：收敛版本（P0）

> 目标：不新增用户功能；先让“合并、升级、权限”正确。
>
> 规模：L；依赖：上游稳定版可合并。

#### 0.1 上游合并与迁移桥接

- 从最新 `develop` 拉收敛分支，合并上游稳定版；按仓库规则 `feature/* → develop`，不走 `develop → main` 直推。
- 把 MiniApp 迁移重编号到合并时上游最大编号之后。当前 `tag-v1.0.3b2` 已有 `025_user_mfa`，本分支 `025_light_apps` 不能保留；否则 `_discover()` 抛 `Duplicate migration version`。
- 为旧水位库加**幂等桥接迁移**：`run_migrations()` 只应用 `version > _current_version`，旧 v31 库不会自动补上游 022–025。桥接需同时覆盖上游新增表/列和 MiniApp 旧迁移的幂等 DDL。
- 更新 `AGENTS.md` schema 版本与 `tests/unit/db/test_db_pool.py` 断言。
- **验收**：全新 SQLite、全新 PostgreSQL、旧 v31 库、上游 v24/v25 库四条路径都能启动；`uv run pytest tests/unit/db -q` 全绿。

#### 0.2 专家可见性统一到上游分组模型

- 引入 `AgentAccess`/`GroupAccess` 适配器，替换 `AppService.is_agent_accessible` 的 `is_shared` 判断；`app_*` 工具、访问申请、权限卡片统一走它。
- 访问申请：`app_id` 存在时从 app 推导 `agent_id`，或严格校验 `row.agent_id == agent_id`。
- Owner 分裂：app owner ≠ agent owner 时，增加双 owner 通知/两步批准；不要在无法共享专家时把申请标记为 approved。
- 专家访问优先走分组/显式共享；应用 ACL 按用户授予。
- **验收**：分组受限专家对组外用户不可见；组外用户申请不会出现“approved 但进不去”；补单测/集成测试。

#### 0.3 能力清单收口

> 状态（2026-11-15）：`LEGACY_CAPABILITIES` 冻结与高风险能力工具侧限制均已落地。

- `LEGACY_CAPABILITIES` 冻结为 R1 能力集合；新增 `external.fetch`/`events.emit`/`schedules.manage` 不自动授予旧应用和 v1 备份恢复。
- `app_set_capabilities`/`app_create(capabilities=...)` 工具侧只允许低风险能力；高风险能力由 Owner 在 Dashboard 开启或走 HITL。
- 增加 `CAPABILITIES_VERSION`，写入 manifest/`me()`；能力变更写审计。
- **验收**：新增能力常量后旧应用解析结果不变；Agent 不能静默开启高风险能力；补 `tests/unit/apps/test_capabilities.py`。

#### 0.4 权限语义收口

> 状态（2026-11-15）：HTTP 与 Agent 工具的管理动作已限制为 owner/admin；`edit_schema` / `manage` 拆分待做。

- 短期：删除、交接、分享列表、公开链接创建/撤销限制为 owner/admin；`edit_structure` 保留给 schema/数据处理。
- UI 文案把 `edit_structure` 描述为“可编辑结构”，不再暗示全权；分享弹窗对高风险授权给出明确警告。
- 中期：拆 `edit_schema` 与 `manage`，或把 `edit_structure` 改名为 `manage`。
- **验收**：协作者不能删除/转交/发布/改分享；补 API 权限测试。

**Phase 0 退出条件**：无新用户功能；升级矩阵通过；权限/能力/访问申请测试通过；`make all` 绿。

---

### Phase 1：一致性与可维护性（R2 前置）

> 规模：M；依赖：Phase 0。

#### 1.1 公开面 PII 只收紧、不放松

> 状态（2026-11-15）：已落地，`public_apps.py` 按快照 PII ∪ 实时 PII 脱敏；补了“清标记不暴露、重新发布才解除”的回归测试。

- 公开 rows 的脱敏列改为 **快照 PII ∪ 实时 PII**；解除 PII 分类必须重新发布。
- **验收**：发布后清掉 PII 标记，公开 rows 仍脱敏；重新发布后才解除。

#### 1.2 访问申请修正

> 状态（2026-11-15）：已改为从 app 推导 agent，app/agent 不匹配不再能共享无关专家；批准/拒绝会向申请人推送 `app_access_resolved`；HTTP 管理员写操作已写 `miniapp.admin_override`；批准与 ACL 写入已合并为单事务。owner 分裂与唯一 pending 索引已完成（schema v35）：`app_owner_user_id` / `agent_owner_user_id` 双 owner 通知，两步批准，只有双方条件都满足才标记 `approved`。

- 单事务完成 `pending → approved` 与协作者 ACL 写入；加 `pending` 唯一索引，防止重复申请。
- 申请人收到批准/拒绝通知，复用 `/api/notifications/ws`。
- 管理员 HTTP 写操作补 `miniapp.admin_override` 审计，与工具侧统一。
- **验收**：并发 approve/reject 不出现“状态 rejected 但 ACL 已授予”；app/agent 不匹配被拒；申请人收到结果。

#### 1.3 结构与性能

> 状态（2026-11-15）：`build_app_service()` 工厂、主列表并发读 preview、Python/TS 沙箱 parity 测试已落地；`Detail.tsx` 已拆出接口/数据/分享/发布/访问申请/能力组件（2,980 → 2,386 行），助手与信息抽屉仍待拆。

- 增加 `build_app_service(...)` 工厂，收敛 6 处手工构造。
- `list_views_with_stats()` 并发读 preview，避免远程后端串行 N 次 round trip。
- `Detail.tsx` 按 Tab 拆出接口/数据/助手/分享；为 R2 的“服务/事件/自动化”面板预留位置。
- 加 Python/TS 沙箱 parity 测试：CSP、wrapper `frame-src 'none'`、二次 load 熔断、WebRTC 冻结关键 API 两边一致。
- 已暴露 `data_version`（tables + `GET /apps/{id}/data-version`），宿主 5 秒轮询并发送 `data-changed`；为后续本地缓存做准备。
- **验收**：`make all`；主列表不再串行；组件测试绿。

---

### Phase 2：R2.0 内置数据集服务

> 规模：M；依赖：Phase 0/1；目标：小应用零部署，MiniApp 不拥有核心数据。
>
> 状态（2026-11-15）：`builtin.dataset` 只读契约（`describe` + `query`）与查询 API 已落地；`light_app_bindings`（schema v32）与 `/bindings` API 已落地，内置数据集以隐式 `dataset` binding 暴露；`row_scope` 的 `app`/`user` 已落地（schema v33，读/写/导出/公开面均已生效）；声明式规则 v1（状态机，schema v34）已落地；promote 第一版（dataset JSON 导出 + 导入新应用）已落地，外部 binding 导入后默认停用。R2.0 收尾已完成：Agent `app_list_bindings`、详情页“服务”面板、`Detail.tsx` 抽屉拆分、小应用零后端验收测试。`published` scope 已取消（公开面由 `public_visible` 控制）；计算字段、触发器与实际 external 切换按决策记录延后（见 `docs/miniapp-platform-roadmap.md` §2 决策 10）。

- 把现有 `light_app_rows` / `light_app_tables` 正式化为 `builtin.dataset` 服务：schema、CRUD、query、aggregate、import/export、ACL、备份、公开投影。
- 新增 `light_app_bindings`（`provider=builtin|connector|external`），装配清单统一引用 binding；小应用默认绑定 `builtin.dataset`，外部绑定指向 R2.1 的 `light_app_services`。
- 表级 `row_scope`：`app`（团队台账）、`user`（个人台账，普通用户只看自己的行）。公开面由 `public_visible` 控制。
- 声明式规则：校验、必填、枚举、状态机、计算字段/汇总、触发器（`on_create → emit event / notify / audit`）；不提供任意服务端 JS。
- 内置数据集的导出/promote 路径先做设计：schema + rows + bindings 可导出，后续可切换到 `external` 服务。
- **验收**：小应用无需任何外部后端即可完成 CRUD、查询、聚合、导入、分享、公开只读；`row_scope=user` 不串数据；绑定模型可同时指向 builtin/connector/external。

#### R2.0 剩余执行计划（2026-11-15）

**范围判定**

| 任务 | 结论 | 理由 |
|---|---|---|
| `published` scope | **已取消（2026-11-15）** | `public_visible` 已表达公开面；`row_scope` 管内部可见性，二者正交。只公开部分行时建独立公开表/投影，不要塞进 row_scope，也不新增 `internal_visible` |
| 计算字段 | **暂不做** | 需要受限表达式引擎；先收集真实字段需求，避免把 MiniApp 变成低代码平台 |
| 触发器（on_create/on_update） | **移到 R2.2** | 依赖事件平面；R2.0 不在同步写路径上造事件/通知副作用 |
| 实际切换到 external 服务 | **移到 R2.1** | R2.0 的 dataset export/import 已提供迁移输入；实际切换需要服务契约、凭据、健康检查和双读校验 |
| bindings 前端/Agent 工具 | **已做（2026-11-15）** | Agent 只读 `app_list_bindings`；详情页“服务”面板 |
| 助手/信息抽屉拆分 | **已做（2026-11-15）** | `AppAssistantDrawer.tsx` / `AppInfoDrawer.tsx`；`Detail.tsx` 降至 ~1,700 行 |

**Sprint R2.0 收尾（建议 3–5 天）**

1. **Bindings surface**
   - Agent 只加只读工具 `app_list_bindings`；不提供 Agent 写 binding，外部服务绑定继续由 owner/admin 在 Dashboard 操作。
   - 前端详情页新增“服务”面板：列出内置数据集 + connector/external binding，owner/admin 可新增、启停、删除；外部 binding 默认停用。
   - 验收：owner 可添加 disabled external binding；`edit_structure` 协作者只读；删除内置 `dataset` 被拒。
2. **Detail drawer 拆分**
   - 抽出 `AppAssistantDrawer.tsx`、`AppInfoDrawer.tsx`；`Detail.tsx` 已降到 ~1,700 行（剩余主要是页面 Tab 与数据编辑弹窗）。
   - 验收：现有 `Detail.test.tsx` + 全 Apps 测试绿；`tsc -b` 通过。
3. **R2.0 验收**
   - 小应用零后端闭环：创建 → 多表 → `row_scope=user` → 状态机规则 → binding 面板 → export/import → 分享/公开只读。
   - 已由 `tests/integration/test_apps_api.py::test_r2_zero_backend_small_app_acceptance` 覆盖。
   - `make all` + `tests/unit/i18n` + 浏览器沙箱回归。
4. **决策记录**
   - 在 `docs/miniapp-platform-roadmap.md` 记录：`published` scope 取消/延后、计算字段延后、触发器归 R2.2、external 切换归 R2.1。

**Release gate（P0，必须先于发布）**

1. **访问申请 owner 分裂 + `pending` 唯一索引（已完成 2026-11-15）**
   - migration v35 已落地；唯一索引使用 partial index（`WHERE status='pending'`），按 app 请求 / agent-only 请求拆成两个。
   - 验收：`test_access_request_split_owners_*`、`test_access_request_race_returns_the_unique_pending_row`、`test_v35_splits_owners_and_dedupes_pending` 覆盖；并发申请不重复；app owner ≠ agent owner 时不会出现“approved 但进不去”。
2. **上游合并与迁移重编号/桥接**
   - 当前分支到 v34；上游已发布 `025_user_mfa` 等。合并时把 MiniApp 迁移整体重编号到上游最大号之后，并为旧水位库加幂等桥接。
   - 验收：全新 SQLite/PostgreSQL、旧 v34 库、上游 v24/v25 库四条路径都能启动。
3. **R2.0 发布验证**
   - `make all`、Windows CI、PostgreSQL、升级矩阵、沙箱回归。

**R2.1 后续**

- 只读外部服务（I1a）→ 事件（I2/I3）→ 调度（I4）。
- 计算字段/触发器在事件平面稳定后做。
- 公开看板（P3）最后。

**PR 顺序**

| 顺序 | PR | 内容 | 依赖 |
|---|---|---|---|
| 1 | `feat: miniapp bindings surface` | 服务面板 + 只读 Agent 工具 | 无迁移 |
| 2 | `refactor: extract miniapp drawers` | 助手/信息抽屉拆分 | 无迁移 |
| 3 | `test: r2.0 acceptance` | 小应用零后端闭环测试 | PR1/PR2 |
| 4 | `fix: access request owner split and pending index` | owner 分裂 + 唯一索引（已完成，migration v35） | — |
| 5 | `chore: sync upstream and renumber migrations` | 合并上游、重编号、桥接（暂缓） | PR4 |
| 6 | `chore: r2.0 release verification` | 全矩阵验证 | PR5 |

> 上游重编号暂缓；在恢复合并前冻结新迁移，避免迁移历史继续分叉。

---

### Phase 3：R2.1 外部服务绑定

> 规模：L；依赖：Phase 2；能力 `external.fetch` 默认关闭。
>
> 状态（2026-11-15）：schema v36、`light_app_services` / `light_app_service_cache`、声明 CRUD、connector/external 只读调用、URL 前缀/工具白名单、TTL 缓存、每服务限流、页面 `octopApp.service().call()` / `.image()`、Agent `app_add_service` / `app_list_services` / `app_remove_service`、图片资产、公开 asset 端点与公开缓存调用已落地；promote 切换待做。

- 新表：`light_app_services`、`light_app_service_cache`；服务声明支持 `provider=connector|external`。
- 服务声明：连接器实例归属、只读工具白名单、URL 前缀、SSRF 校验、响应 ≤5 MB、超时、每服务限流。
- 缓存：TTL + 定时清理 + 单服务配额；缓存键含 service/tool/args；公开链接只读缓存，不发起 live 调用。
- 资产：服务端拉图 → 存 `assets/` → 页面 `octopApp.asset()`；公开面加只读 asset 端点。
- Agent 工具：`app_add_service` / `app_list_services` / `app_remove_service`。
- 支持 binding 从 `builtin.dataset` 切换到 `external` 服务（promote 路径的第一版）。
- **验收**：允许的只读工具可调用；未声明工具/URL 被拒；SSRF 被拒；缓存命中；公开页不能 live 调用；binding 切换后 UI/装配清单不变。

---

### Phase 4：R2.2 事件

> 规模：L；依赖：Phase 2/3；先修 `app.import` 顺序问题。
>
> 状态（2026-11-15）：schema v37、`light_app_events`、外部事件 `app.inbox` 投递、游标读取/ack、SDK `events()` / `ackEvents()`、`on_create`/`on_update` 触发器、`app.import` 幂等 append 已落地；出站事件和 iframe 主动推送待做。
> 已知缺口：跨 app 幂等、`ImportResult` 未落 delivery、`app_event` 帧无消费者，见
> [event-model.md](./event-model.md) §5。

- 先修 `app.import` 的并发/乱序/`replace`：按 app/table 串行，或改成幂等 upsert。
- `light_app_events` + `GET /events?since=` + ack；保留 500 条/30 天，加清理任务。
- 推送优先复用 `/api/notifications/ws`，页面轮询只做可见性兜底；不要每个 App 独立 5–10 秒轮询。
- 出站 webhook：HMAC、私网/SSRF 校验、每 app 30 条/分钟、死信可查；能力 `events.emit`。
- **验收**：重复投递幂等；乱序不覆盖新数据；推送/轮询都可用；公开链接没有事件接口。

---

### Phase 5：R2.3 调度与工作流

> 规模：M；依赖：Phase 2–4 的服务与事件动作。

- 抽共享调度原语：DB 租约 + `next_run_at` + 失败退避；不要复用当前“前推一个间隔”的 claim 语义。
- `light_app_schedules`：先支持 `sync_source`、`snapshot_publication`；`run_agent` 延后到配额/审批模型之后。
- 迁移旧 `light_app_sources.schedule_seconds`，旧列保留一个版本。
- 最小 workflow runner：`light_app_workflows` / `light_app_runs` / `light_app_run_steps`；动作先支持 `call_service`、`wait_event`、`approve`、`notify`、`update_dataset`；复用 `infra/events`、调度器和 `hitl_pending`，不造通用 BPM 引擎。
- **验收**：多实例只执行一次；时区用 `config.default_timezone`；旧数据自动迁移；长任务不会并发执行；workflow 重试幂等、可恢复。

---

### Phase 6：R2.4 公开看板 / R3 分发

> 规模：L；依赖：Phase 2–5、公开面 PII 策略。

**R2.4 公开看板**

- 每个单元格必须已有有效发布快照；board token 可撤销/轮换；隐藏表、PII、撤销立即生效；限流。
- 公开实时外部调用仅限结构化 adapter + `public_live=1`；通用 `http-extract` 永不参与公开实时。

**R3 Bundle v2（先做）**

- 导出/导入 HTML、schema、行、assets、capabilities、装配清单；导入时重新 `validate_html`。
- 缺失 capabilities 用**安全默认集**，不是 legacy 全开；services/schedules 默认 disabled；connector 标记 `needs_binding`。
- 可评估 SQLite 作为 Bundle 内快照格式，但不改变线上存储。
- 内置数据集可导出为 Bundle 或 promote 到外部服务；两者共享 schema/rows 导出。

**R3 IM 认证链接（后做）**

- 不要用会被链接预览消耗的 `GET /auth/app?code=`；改成中转确认页/POST，单次消费、短 TTL、`no-referrer`。
- 群聊/非 owner 只发公开只读链接；渠道身份绑定最后做。

---

## 4. PR 顺序

| 顺序 | PR | 内容 | 验收 |
|---|---|---|---|
| 1 | `chore: sync upstream and bridge migrations` | 合并上游、重编号、桥接迁移 | 四条升级路径启动；`tests/unit/db` 绿 |
| 2 | `fix: unify expert audience for MiniApps` | `GroupAccess`、访问申请双 owner | 分组/owner 分裂测试 |
| 3 | `fix: freeze legacy capabilities` | legacy 集合冻结、高风险能力 Owner 批准 | 新能力不自动下发 |
| 4 | `fix: restrict app management permissions` | 删除/交接/发布/分享限 owner/admin | 权限测试 |
| 5 | `fix: public PII and access-request atomicity` | PII 并集、事务、通知、审计 | 回归测试 |
| 6 | `refactor: app service factory and detail tabs` | 工厂、并发 preview、前端拆分 | `make all` + tsc |
| 7 | `feat: builtin dataset service` | R2.0：`builtin.dataset` 契约、binding、row_scope、声明式规则、导出/promote 设计 | 小应用零后端 CRUD/查询/聚合/分享/公开只读 |
| 8 | `feat: miniapp external services` | R2.1：`connector/external` 绑定、缓存、SSRF、promote 切换 | 服务/缓存/SSRF 测试 |
| 9 | `feat: miniapp events` | R2.2 | 顺序/幂等测试 |
| 10 | `feat: miniapp schedules and workflows` | R2.3：调度原语 + 最小 workflow runner | 多实例租约/重试测试 |
| 11 | `feat: public boards` | R2.4 | 撤销/隐藏/PII 测试 |

> 进度（2026-11-15）：PR10 的调度原语已落地（schema v38）：`light_app_schedules`、DB 租约 + 心跳、`sync_source` / `snapshot_publication`、`GET/POST/PATCH/DELETE /api/apps/{id}/schedules`、旧 `light_app_sources.schedule_seconds` 自动迁移、公开链接弹窗自动刷新开关；可靠性补强（完成时间重排、动作硬超时、确定性抖动、失败状态重置、运行/超时/积压指标、弹窗展示下次运行与错误）已完成；数据源同步已按批次内容幂等，超时/崩溃重试不重复；source 级分布式锁与运行历史待做；workflow runner 待做。PR3（能力冻结/高风险限制）、PR4（管理权限限制）已落地；PR5 的公开面 PII、访问申请 app/agent 推导、结果通知、HTTP 管理员写审计与原子批准已落地；PR6 的 `build_app_service()` 工厂、主列表并发 preview、沙箱 parity 测试、`Detail.tsx` 主要 Tab/弹窗拆分已落地；PR7 的 `builtin.dataset` 只读契约、`light_app_bindings`（schema v32）、`row_scope=app|user`（schema v33）、声明式规则 v1（schema v34）、dataset 导出/导入、bindings 面板与 Agent 只读工具、抽屉拆分已落地，R2.0 零后端验收测试通过；`published` scope 已取消；计算字段/触发器/实际 external 切换按决策延后；owner 分裂与唯一 pending 索引（schema v35）已落地；PR8 的外部服务声明/缓存（schema v36）、只读调用、页面 SDK、Agent 三个服务工具已落地，图片资产/公开缓存/promote 待做；PR1/PR2 依赖上游合并，按当前决定暂缓。

---

## 5. 决策门

1. **G1 迁移门**：迁移矩阵全绿之前，不合并、不发布。
2. **G2 权限门**：专家可见性、`edit_structure` 语义、能力提升方式、内置数据集边界确认之前，不开 R2.0/R2.1。
3. **G3 事件门**：事件顺序/去重方案确认之前，不做事件 inbox/outbox。
4. **G4 前端门**：`Detail.tsx` 拆分和 `AppService` 工厂完成之前，不往详情页加新面板。
5. **G5 存储门**：共享 DB 仍是默认；每 App SQLite 只在单实例/离线/强隔离版本重新评估，且需先有 `AppDataStore` 抽象和基准测试。
6. **G6 退出路径门**：内置数据集没有 schema/rows/bindings 导出与 promote 路径之前，不把它作为默认后端。

---

## 6. 暂不做清单

- 每 App 一个 SQLite：不作为默认服务端存储。
- 页面直连 localStorage/IndexedDB：最多做宿主代理的 `octopApp.storage` 小缓存，且要等能力版本化之后。
- Electron 运行时：只做薄桌面壳时才考虑，不把 MiniApp 运行时搬进 Electron。
- `run_agent` 定时任务：等配额与审批模型。
- 公开实时任意 URL：只允许结构化 adapter + `public_live`。
- 云端模板市场：不进入 R2 关键路径。

---

## 7. 验证

```bash
uv run pytest tests/unit/db tests/unit/apps -q
uv run pytest tests/integration/test_apps_api.py tests/integration/test_boards_api.py -q
uv run pytest tests/unit/i18n -q
uv run pytest tests/integration/test_miniapp_sandbox.py -q
make all
cd dashboard && npx tsc -b
```

手工验证：

1. 全新 SQLite / PostgreSQL 安装。
2. 旧 v31 库升级、上游 v24/v25 库升级。
3. 分组受限专家：组外用户不可见、申请不会“approved 但进不去”。
4. 发布后清掉 PII 标记：公开 rows 仍脱敏；重新发布后才解除。
5. 并发 approve/reject：状态与 ACL 一致。
6. 应用列表在远程后端下不串行等待。
7. 小应用只绑定 `builtin.dataset`，无任何 external 服务，也能完成 CRUD、查询、聚合、分享、公开只读。
8. `row_scope=user` 时普通用户只看自己的行；owner/admin 看全部。
9. 内置数据集导出 schema/rows/bindings 后，可 dry-run promote 到 external 服务，UI/装配清单不变。

---

## 8. 风险与开放问题

| 问题 | 现状/建议 | 需确认 |
|---|---|---|
| 权限拆分 | 短期限制管理端点，中期拆 `edit_schema`/`manage` | 是否接受 API 破坏性变更 |
| 访问申请 owner 分裂 | 已实现双 owner 通知/两步批准；app owner 只能完成 app 半边，专家未共享时保持 pending | 产品是否允许 app owner 单独批准（当前结论：仅当专家已可访问时才可单独完成） |
| 事件推送 | 复用 notification WS，轮询兜底 | WS 是否覆盖所有客户端形态 |
| Bundle 格式 | CSV 保持现状；SQLite 快照可选 | 是否需要 schema/类型/索引保真 |
| 本地缓存 | 暂缓；如做则宿主代理 + 配额 + TTL | 是否有真实页面状态/离线需求 |
| 内置数据集边界 | 小应用默认 `builtin.dataset`；大应用 promote | 配额阈值与 promote 触发条件 |
| 内置数据集膨胀 | 防止变成通用业务库：配额、监控、导出/promote | 是否需要硬性行数/QPS 上限 |
| 小应用逻辑 | 声明式规则 + 最小 workflow，不提供任意服务端 JS | 规则表达力是否足够 |
| 公开看板 | R2 最后 | 是否与公开实时一起滑到 R3 |

---

## 9. 相关文档与代码索引

| 关注点 | 位置 |
|---|---|
| 当前功能/架构状态 | `docs/miniapp-overview.md` |
| R2/R3 需求与决策 | `docs/miniapp-platform-roadmap.md` |
| 事件协议讨论 | `docs/miniapp-event-api-design.md`、`docs/external-events-design.md` |
| 沙箱加固 | `docs/miniapp-sandbox-hardening.md` |
| 存储与行写入 | `src/octop/infra/apps/service.py`、`src/octop/infra/db/repos/apps.py` |
| 内置数据集/绑定（目标） | `src/octop/infra/apps/`（建议新增 `dataset_service.py`、`bindings.py`） |
| 迁移 | `src/octop/infra/db/migrations/`、`src/octop/infra/db/migrate.py` |
| 应用 HTTP | `src/octop/api/routers/apps.py`、`app_access.py`、`public_apps.py` |
| 能力清单 | `src/octop/infra/apps/capabilities.py`、`dashboard/src/components/MiniAppFrame/capabilities.ts` |
| 沙箱 | `src/octop/infra/apps/sandbox.py`、`dashboard/src/components/MiniAppFrame/` |
| 前端工作台/详情 | `dashboard/src/pages/Apps/` |
| 分支与发布 | `AGENTS.md` §10、`CONTRIBUTING.md` |
