# MiniApp 用户管理与接入方案（v3）

> Status: design proposal / revised draft  
> Date: 2026-11-21  
> Branch: `feature/miniapp-capabilities`  
> Scope: MiniApp 用户管理、Octop 用户接入、IM 渠道接入  
> Companion deck: [`docs/slides/miniapp-identity-access-plan.html`](./slides/miniapp-identity-access-plan.html)  
> Implementation plan: [`docs/miniapp-identity-implementation-plan.md`](./miniapp-identity-implementation-plan.md)

---

## 0. 结论与 v2 反思

### 0.1 结论

MiniApp 缺的不是一个“用户管理页面”，而是三层能力，并且每一层只能有一个事实源：

| 层 | 事实源 | 不做什么 |
|---|---|---|
| 身份层 | `PrincipalDirectory` + `users.kind`（第一版实现） | 不让业务代码直接查 `users` 判断主体 |
| 凭证层 | `platform` / `app` / `guest` 三种 JWT scope | 不把“能进哪个应用”编码进平台 JWT |
| 授权层 | `infra/apps/access.py` 的单一 `authorize()` | 不在路由、Agent 工具、IM 工具里各写一套 ACL |

一句话：**平台账号决定“谁能进 Octop”，应用成员 + 表可见性决定“谁能用哪个应用、看哪张表、哪一行”，IM 绑定只决定“这个聊天账号是谁”。三层不能互相推导。**

推荐方案：

- **主体**：`member` / `guest` / `submission` / `anonymous` 四类；guest 是持久参与者，submission 是一次性提交者。
- **凭证**：平台 JWT（仅 member）、app-scoped JWT（member / guest，单应用）、guest-scoped JWT（仅 guest，只开 guest 门户）。P1 不用 cookie。
- **成员**：`light_app_collaborators` 承载成员、角色、状态；guest 只能持有 `participate`。
- **表**：`light_app_tables.member_visible` 只约束 `participate`（含所有 guest）。
- **行**：`light_app_rows.row_owner_user_id` 与 `created_by` 分离；user-scoped 表按行归属过滤。
- **PII**：行级 `can_view_pii`；自己的行不脱敏，共享表 / 他人行脱敏。
- **IM**：P2a 只做链接交付 + 工具面收口；P2b 才做 per-sender actor，前提是 thread / memory 隔离。

### 0.2 v2 反思：八个问题与根因

v2 的方向（主体抽象、成员 + 表可见性 + 行所有权、统一授权）成立，代码事实也基本准确；问题出在**把身份、凭证、授权三层混在一起讨论**，每层单独看都对，合起来互相矛盾。

| # | v2 的问题 | 根因 | v3 修正 |
|---|---|---|---|
| 1 | guest 会话三条约束互斥：per-app 会话、非 app scope 拒绝 guest、`/api/guest/apps` 跨应用门户不能同时成立 | 只定义了 app scope，没有定义“guest 自己”的凭证 | 新增 `guest` scope，只允许 `/api/guest/*`；app token 仍单应用 |
| 2 | “WebView 优先 HttpOnly cookie”在当前沙箱下不成立 | 没核对 app iframe 的 `sandbox` 与 app-scope 启动接口白名单 | P1 改为按 `app_id` 存 token；cookie 只作为真实外部 WebView 的 P3 实验 |
| 3 | guest 可持 `view`，绕过 `member_visible` | 角色与主体类型混用 | guest 只能持 `participate`；`view` 是平台成员专属 |
| 4 | `authorize()` 迁移没排期，`participate` 一加就锁死或放权 | 低估了 `PERMISSION_RANK` 的调用面 | P0 必须清完所有 rank 比较；新增路由 action 注册表测试 |
| 5 | `app_access_requests.kind` 承载不了外部邀请 | 表要求 `requester_user_id NOT NULL`，外部人还没有 `users` 行 | 新增 `app_member_invites`；`app_access_requests` 不改 |
| 6 | submission 无法落到整数 `row_owner_user_id` | 一次性主体没有 user id | 新增 `submitter_ref` + receipt token |
| 7 | PII 行级依赖行所有权，但 `row_owner` 排在 P1 | P0 / P1 交付顺序倒置 | `member_visible` 与 `row_owner_user_id` 的 schema 提前到 P0 |
| 8 | P2a 只封了 `app_share_link`，没封 app 数据工具；配额承诺没有基础设施 | 把“规则”当成了“强制点” | P2a 增加工具面收口；per-action 配额单独立项 |

### 0.3 v2 → v3 关键变更

| 主题 | v2 | v3 |
|---|---|---|
| guest 角色 | 可持 `view` / `participate` | **只能持 `participate`** |
| guest 凭证 | per-app cookie + refresh | **`guest` scope + app token；无 cookie** |
| 会话存储 | 单 `auth_token` + app scope | **按 `app_id` 存 token；guest token 独立** |
| 外部邀请 | `app_access_requests.kind=join` | **`app_member_invites` + `collaborators.status`；不改 access_requests 表** |
| 行所有权 / 表可见性 | P1 schema | **P0 schema**（先于 PII 行级） |
| PII 响应 | 只有 `pii_columns` | **加 `masked_pii_rows` / `masked_pii_columns`** |
| P2a | 链接交付 | **链接交付 + 所有 app 数据工具 owner-only + `/link` 握手** |
| 配额 | “都要有配额” | **P1 只做 guest AI 默认关闭；per-action 配额单独立项** |
| 公开面 | “公开路由只有 GET” | **修正：有只读缓存 POST；仍无写行能力** |
| `org` 策略 | P1 枚举 | **移除，P3 与用户组 / SSO 域一起做** |

---

## 1. 背景与问题

### 1.1 四类使用者

| 使用者 | 现在怎么进 | 缺口 |
|---|---|---|
| 创建者 / 同事（Octop 用户） | Dashboard 登录 + 应用 ACL | 成员管理只有 `PUT /shares` 全量替换，无法承载 guest |
| 外部参与者（无 Octop 账号） | 只能拿 `/p/{token}` 公开只读链接 | 不能写、不能看自己的提交；公开表会暴露整张表 |
| IM 非 owner 发送者 | 所有外部 IM 发送者按 agent owner 执行 | 群成员可能借 Agent 以 owner 身份操作应用；个人链接只信任 owner |
| 一次性提交者 / 匿名访客 | 公开路由只读 | 无法提交；为每个填表人建持久 guest 又太重 |

根因：`users.id` 同时承担了“平台账号”和“应用用户”，缺少 **应用成员 + 表级可见性 + 主体抽象**。

### 1.2 现状代码事实（2026-11-21 工作树）

| 层 | 代码事实 | 对方案的含义 |
|---|---|---|
| 平台用户 | 没有自助注册；只有 setup、管理员创建、邀请码、SSO 自动开通。guest 若走 `users`，必须被平台侧过滤。`src/octop/api/routers/auth.py`、`src/octop/infra/users/invites.py` | 不能假设外部参与者先注册 Octop 账号；guest 不能进入登录 / 用户列表 / setup 计数 |
| 平台用户缓存 | `UserManager.boot` 把 `user_repo.list()` 全部装入 `_users`；`get_by_id` 只扫内存。`src/octop/infra/users/manager.py:104-115`、`:219-223` | 若 boot 过滤 guest，guest app token 必须由 `PrincipalDirectory` 从 repo 解析，不能复用 `get_by_id` |
| 应用 ACL | `permission_for(row, user_id)`；`MAX_COLLABORATORS = 50`；`PUT /shares` 全量替换，`set_collaborators` 先 `DELETE` 全部。`src/octop/infra/apps/service.py:1114-1125`、`src/octop/infra/apps/models.py:46`、`src/octop/infra/db/repos/apps.py:1163-1181` | 旧分享弹窗保存一次会删掉 guest；`/shares` 必须保留 guest 行 |
| 权限阶梯 | `PERMISSION_RANK` 在 `service.py:1136`、`:1487`、`:1636-1639`、`:1667`、`tools.py:297`、`:334`、`integration_ops.py:128` 参与判定 | 新增角色不能只加 rank；必须把数据访问判定收口到 `authorize()` |
| 授权细节 | PII 有 4 处 `permission == "view"`；`include_all_rows` 只给 owner/admin；app 级表 participant 能读全表、能导出。`src/octop/api/routers/apps.py:2185`、`:3177`、`:3347`、`:3413`、`:3178`、`:3282`、`:3314`、`:3419` | `participate` 会绕过 PII 脱敏；必须先做行级 PII + 表级可见性 |
| 授权调用面 | `api/routers/apps.py` 有 86 条路由、34 处 `_row_access`；Agent 工具另有 `_require_app`。`src/octop/infra/apps/tools.py:283-297` | P0 的“单一授权内核”是跨文件改造，不是新增一个函数 |
| 访问申请 | 双 owner 审批；专家 owner 那半 = 全局 `agents.is_shared=1`；拒绝不回滚；`requester_user_id NOT NULL`。`src/octop/infra/apps/service.py:1616-1623`、`:1584-1602`、`src/octop/infra/db/migrations/031_app_access_requests.sql:10` | 不能复用于外部参与者；外部邀请需要独立表 |
| 凭证 / 前端 | app JWT 的 `perm` claim 未使用，授权按实时 ACL；`_APP_SCOPE_READ_PATHS` 只允许少量非 app 子树启动接口；前端只有一个 `auth_token` + `octop_app_scope`（“one MiniApp only”）。`src/octop/api/deps.py:121-190`、`dashboard/src/api/request.ts:7-22` | 撤销即时生效；但多应用 guest 会话会互相覆盖，且 cookie 路径作用域会排除启动接口 |
| 沙箱 | app HTML 跑在 `<iframe sandbox="allow-scripts allow-forms">`（无 `allow-same-origin`）；API 调用由父窗口 postMessage 代理。`src/octop/infra/apps/sandbox.py:248`、`dashboard/src/pages/Apps/Detail.tsx:541-545` | app iframe 不直接持凭证；cookie 不能作为 P1 的会话基础 |
| 公开面 | 公开链接有 `GET /{token}`、`GET /{token}/rows`，以及只读缓存的 `POST /{token}/services/{name}/call`；均不写行。`src/octop/api/routers/public_apps.py:117-240` | “公开路由只有 GET”不准确；公开面确实没有行写入 / 提交能力 |
| IM 身份 | IM 会话归属 agent owner；`channel_identities` 没有 bot / account 维度；飞书话题私聊 subject 是 `thread_id`，不是 `open_id`；个人链接只信任绑定到 owner 的私聊。`src/octop/infra/gateway/process/message_keys.py:79-105`、`src/octop/infra/db/migrations/045_app_setup_plans.sql:47-58`、`src/octop/infra/auth/channel_binding.py:72-90` | 稳定身份键是 P2 前置；per-sender actor 还要解决 owner-scoped thread |
| 行写入 | `light_app_rows.created_by` 可空、无 FK；导入在 user-scoped 表上默认归 owner；恢复把 `created_by` 设为恢复者。`src/octop/infra/db/migrations/028_light_app_rows.sql:13-27`、`src/octop/infra/apps/service.py:4884-4889`、`:5788` | `row_owner` 必须覆盖所有写入路径，并定义 NULL 语义 |

### 1.3 v2 的教训

1. **凭证作用域先于身份表。** 只要 guest 拿不到平台 JWT，`users.kind` 的绝大多数守门点就只是纵深防御，而不是主边界。反过来，先纠结“guest 放不放进 users”，会忽略真正决定安全性的凭证 scope。
2. **规则必须落到强制点。** “chat 不返回个人数据”如果不封 app 数据工具，就只是一句提示词。
3. **排期必须服从依赖。** 行级 PII 依赖行所有权；外部邀请依赖一张能描述“还没有账号的人”的表。

---

## 2. 目标与非目标

### 2.1 目标

- 外部参与者能用最小身份进入指定 MiniApp，只看到被允许的表和行。
- Octop 用户能在不共享 Agent 的情况下使用他人的应用。
- IM 成为身份来源和入口，而不是第三套账号体系；群聊不泄露个人数据。
- 保持单实例、自托管、SQLite/PostgreSQL 双栈，不引入外部服务。

### 2.2 非目标

- **不是多租户隔离**。MiniApp 文档已明确“不是多租户隔离载体”（`docs/miniapp-overview.md:63`）；guest 只是同一实例内的参与者。
- **P1 不做 cookie 会话**。cookie 与当前沙箱、app-scope 启动接口白名单冲突；留到 P3 单独验证。
- **P1 不做 per-action 配额**。现有只有 lifetime token quota 与粗粒度限流；export / aggregate / service / events 的 per-member 配额单独立项。
- **P1 不做 `org` 访问策略**。没有组织 / 用户组模型；P3 与 SSO 域共享一起做。
- **不在 P2a 做 per-sender 聊天记忆隔离**。P2b 单独评估。
- **不重做全局 `agents.is_shared` 审批模型**。这是独立项目。
- **不覆盖公开面全部 PII 风险**。冻结 HTML、缓存 service、assets 的 PII 过滤单独立项。
- **P1 不做匿名公开写入**。公开表单走 P3 的 submission token。

---

## 3. 身份模型

### 3.1 四类主体

| 主体 | 是否有 `users` 行 | 能否登录 | 能做什么 |
|---|---|---|---|
| `member` | 是（`kind=member`） | 是 | 平台账号、Agent、平台权限；应用访问按成员关系 |
| `guest` | 是（`kind=guest`） | 否 | 持久应用参与者；只能进被邀请应用，只能持 `participate` |
| `submission` | 否 | 否 | 一次性提交；签名令牌绑定 app + table + TTL + 次数 |
| `anonymous` | 否 | 否 | 只有 publication token；只读 `public_visible` 表 |

### 3.2 PrincipalDirectory

业务代码不直接依赖 `users` 表；统一通过一个接口解析主体。**只做身份解析，不做成员管理**：

```python
class PrincipalKind(StrEnum):
    MEMBER = "member"
    GUEST = "guest"
    SUBMISSION = "submission"
    ANONYMOUS = "anonymous"

@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    user_id: int | None          # member / guest
    display_name: str
    app_id: str | None = None    # app scope / submission
    session_id: str | None = None
    submitter_ref: str | None = None

class PrincipalDirectory(Protocol):
    def resolve_token(self, token: str, *, method: str, path: str | None) -> Principal: ...
    def resolve_channel(
        self, channel_type: str, account_key: str, sender_id: str
    ) -> Principal | None: ...
    def get_user(self, user_id: int) -> Principal | None: ...
    def require_member(self, principal: Principal) -> int: ...
```

第一版实现（`src/octop/infra/users/principals.py`）：

- `member` / `guest` 映射到 `users` 行；`users.kind` 区分。
- `submission` 不落 `users`，由签名令牌解析；`anonymous` 由 publication token 解析。
- guest 的解析**绕过 `UserManager` 缓存**：`resolve_token` 用 `user_repo.get` 构造 `User`，检查 `kind='guest'`、`disabled=0`、app 成员关系与 session 状态。
- HTTP 适配层保留 `User` 返回类型（增加 `kind` 字段），路由通过 `principal_of(user)` 取得 `Principal`；不要求所有路由立刻改成 Principal 参数。
- `list_app_members` / `create_guest` 属于应用成员域（`infra/apps/members.py`），不放进 PrincipalDirectory。

### 3.3 guest 行与平台守门

guest 是 `users` 行：

| 字段 | 值 |
|---|---|
| `username` | `guest_<ulid>`，全局唯一、不可自选 |
| `password_hash` | `NULL` |
| `email` | `NULL` |
| `role` | `'user'`（不是 `'guest'`；`role` 是角色模板 public id，不能当主体类型用） |
| `permissions` | `[]` |
| `kind` | `'guest'` |
| `display_name` | 用户输入，限长、可匿名化 |

**主边界是凭证 scope**：guest 永远不会拿到平台 JWT。因此平台路由（`/api/agents`、`/api/users`、`/api/teams`、`/api/experts` 等）对 guest 天然不可达。下列过滤是纵深防御，必须有测试：

- `UserManager.boot / list / count`：只加载 / 统计 `kind='member'`；`get_by_id` 保持 member-only。
- 登录、密码重置、SSO、LDAP、邀请码兑换：拒绝 `kind='guest'`；SSO 不使用 guest 的邮箱 / subject 建号。
- `require_permission`：`kind='guest'` 恒 false；`is_admin` 恒 false。
- Agent 创建 / 团队 / 专家发布：拒绝 guest。
- 用户管理列表、邀请、审计展示、CLI `user list`：默认只列 member。
- setup 用户数、登录前置判断：只统计 member（新增 `user_repo.count_by_kind('member')`）。
- 分享弹窗按用户名添加成员：`get_by_username` 只返回 member，避免把 guest 误加为平台协作者。
- 备份 / 恢复：guest 行随 DB 备份；应用数据集导出不包含成员关系（见 §8.3）。

### 3.4 guest 生命周期与升级

- **创建**：加入码 / 邀请兑换时创建；host 审批通过后激活；写 `joined_via`、`invited_by`、`display_name`。IM 首次绑定通过 P2a 的 `/link` 完成。
- **复用**：同一浏览器再次兑换加入码时，若带有效 `guest` token，复用同一 `user_id`；否则新建 guest。IM 绑定后按 `channel_identities` 复用。
- **活跃**：`light_app_collaborators` 记录角色 `participate` 与状态 `active`；guest 只能持 `participate`。
- **禁用 / 移除**：默认 `users.disabled=1` + 成员关系 `status='removed'`；不硬删除。行归属保留（审计可追溯），`display_name` 匿名化。
- **升级为 member**：同一 `users.id` 设置用户名 / 密码或 SSO / 角色 / 权限；保留成员关系、行归属、行历史。升级需要邀请码或管理员操作，不开放自助注册。
- **跨渠道合并**：同一人多个 IM 身份可绑定到同一 member；guest 跨渠道合并放 P3，需要 `/link` 或平台账号绑定。

### 3.5 为什么第一版仍用 `users.kind`

- `light_app_collaborators.user_id`、`light_app_rows.created_by`、行历史、审计都已经是整数 user id；换成独立 principal 表会让这些 FK 全部多态化。
- guest 永远不进入平台凭证路径，`users.kind` 的横切风险被 scope 边界压住。
- 如果未来 guest 规模或隔离要求上来，`PrincipalDirectory` 可以换成独立 `principals` 表，业务代码不改；`users.kind` 是**第一版实现**，不是临时补丁。

---

## 4. 授权模型

### 4.1 五层判定

```
平台角色 → 应用成员角色 → 表可见性 → 行所有权 → 动作 / PII
```

任何一层都不能单独决定访问。`permission_for` 只保留为 **app 级角色**判定，用于展示与管理排序，不再单独决定表 / 行 / PII。

### 4.2 角色矩阵

| 角色 | 读可见表 | 写自己 | 写全部 | 改结构 | 导出 / 聚合 | 成员 / 发布 |
|---|---|---|---|---|---|---|
| owner / admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| edit_structure | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| edit_data | ✓ | ✓ | 仅 app 表 | — | ✓ | — |
| participate | 仅 `member_visible` | ✓ | — | — | 默认拒绝 | — |
| view（仅 member） | ✓ | — | — | — | 只读导出（脱敏） | — |
| public visitor | 仅 `public_visible` | — | — | — | — | — |

补充：

- **guest 只能持 `participate`**；服务层拒绝把 `view` / `edit_data` / `edit_structure` 授予 `kind='guest'`。
- `member_visible` 约束所有 `participate` 主体（包括平台 member 的 participate）以及 guest；不约束 `view` / `edit_*`。
- `permission_for` 只认 `light_app_collaborators.status='active'`；`pending` / `removed` 不产生任何权限。
- 非 owner 在 `row_scope=user` 表上只看 / 写自己的行；`row_scope=app` 表上按角色看全部。
- 现状 `include_all_rows = is_admin or owner_user_id == user.id`；所以 `edit_data` 协作者在 user-scoped 表上也只读写自己的行。
- agent owner 对托管应用有隐式 `view`（`service.py:1120-1124`），不受 `member_visible` 约束，也不能被授予更低角色；这条要写进测试。

### 4.3 动作模型

`authorize()` 接收明确动作，而不是一个模糊的 permission：

```python
class Action(StrEnum):
    VIEW_APP = "view_app"              # detail / html / files / assets / events
    VIEW_SCHEMA = "view_schema"
    VIEW_ROW = "view_row"              # list / get / search / dataset query
    CREATE_ROW = "create_row"
    UPDATE_ROW = "update_row"
    DELETE_ROW = "delete_row"
    EXPORT = "export"
    AGGREGATE = "aggregate"
    AI = "ai"                          # assistant
    SERVICE_CALL = "service_call"
    EMIT_EVENT = "emit_event"
    MANAGE_STRUCTURE = "manage_structure"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_SHARES = "manage_shares"
    MANAGE_BINDINGS = "manage_bindings"
    MANAGE_INGRESS = "manage_ingress"
    MANAGE_SCHEDULE = "manage_schedule"
    PUBLISH = "publish"
    DELETE_APP = "delete_app"
    VIEW_HISTORY = "view_history"
    RUN_GAME = "run_game"

@dataclass(frozen=True)
class Decision:
    allowed: bool
    role: str | None
    row_scope: Literal["all", "own", "none"]
    pii_policy: Literal["visible", "masked", "mask_unless_own"]
    reason: str

def authorize(
    principal: Principal,
    app: AppRecord,
    action: Action,
    *,
    table_key: str | None = None,
    row: RowRecord | None = None,
) -> Decision: ...
```

HTTP、Agent 工具、IM 工具都调用同一个入口。新增端点必须先补授权矩阵，不能只加一个 `require("view")`。

### 4.4 表级可见性

`light_app_tables.member_visible INTEGER NOT NULL DEFAULT 0`：

- `participate` 只能读 `member_visible=1` 的表；直接访问隐藏表返回 **404 `APP_TABLE_NOT_FOUND`**（不用 403，避免枚举表名）。
- `GET /apps/{id}` 对 `participate` 过滤 `AppDetail.tables`；`columns` 取第一张可见表的 schema（没有可见表则为空）。过滤必须在服务端完成，前端不做安全判断。
- 默认 0；应用 owner 在成员页逐表开启；表单模板可以在创建时显式把表单表设为 1，但服务端默认仍是 0。
- `public_visible` 只影响公开链接，与成员可见性正交。

### 4.5 行所有权

新增 `light_app_rows.row_owner_user_id INTEGER`（无 FK，和 `created_by` 一样不随用户删除而级联）：

- `created_by` 是**执行者**，用于审计；
- `row_owner_user_id` 是**行属于谁**，用于 `row_scope=user` 过滤、guest 自己的数据、Agent 代填、导入 / 恢复；
- 回填 `= created_by`；`created_by IS NULL` 的行保持 `NULL`，在 user-scoped 表里按“归 app owner”处理（只对 owner/admin 可见）。
- 新增所有行时同时写两个字段；`update_row` 不改变 `row_owner`；`delete_row` 按 `row_owner` 判定。
- `import_rows` 增加 `row_owner_user_id`（仅 owner/admin 可显式指定）；默认：操作者；user-scoped 表默认 app owner（保留现有行为）。
- Agent 替 guest 填行（P2b）必须设 `row_owner_user_id = guest`，否则 guest 看不到自己的行。
- 索引：`(app_id, table_key, row_owner_user_id)`。

### 4.6 PII 行级判定

当前 4 处 `redact_pii = permission == "view"` 改成行级能力：

```python
redact_pii = not can_view_pii(principal, app, table, row)
```

规则：

- `owner` / `admin` / `edit_structure` / `edit_data`：当前行为不变，可见全部 PII。
- `view`：始终脱敏（保持现状；own-row 豁免只给有写能力的 `participate`）。
- `participate`：`row_owner_user_id == principal.user_id` 时可见；否则脱敏。
- `anonymous` / public：永远脱敏。
- **响应必须带掩码标记**：列表接口在 `pii_columns` 之外增加 `masked_pii_rows: list[str]`（被脱敏的行 id）；单行接口增加 `masked_pii_columns: list[str]`。否则前端无法区分“真实值”和“掩码值”。
- PII 列的过滤 / 排序 / 聚合：只要主体可能存在被掩码的行（`view` / `participate`），就拒绝用 PII 列做 filter / sort / group，保持现有保护。
- service 层的 `redact_pii: bool` 改为按行策略（`PiiPolicy`）：读路径逐行计算，而不是整表一个 bool。涉及 `_apply_pii`（`service.py:1010-1017`）、`read_rows`（`:3129-3179`）、`export_rows`（`:5377-5400`）、Agent 工具 `_may_see_pii`（`tools.py:331-334`）。

### 4.7 授权内核迁移（P0 的核心）

`PERMISSION_RANK` 的比较必须从数据访问路径清掉，只保留在“谁能授予谁什么角色”的管理逻辑里。P0 的完成标准是**可 grep 验证**的：

```bash
# 数据访问路径不再出现 rank 比较
grep -rn "PERMISSION_RANK" src/octop/api src/octop/infra/apps/tools.py \
  src/octop/infra/apps/integration_ops.py
# 不再有请求级 PII exact check
grep -n 'permission == "view"' src/octop/api/routers/apps.py
# 每条 app 路由都有 action 注册
# tests/unit/api/test_app_route_actions.py 枚举路由并对照 APP_ROUTE_ACTIONS
```

迁移方式：

1. 新增 `src/octop/infra/apps/access.py`，实现 `Action` / `Decision` / `authorize()`。
2. `AppService.authorize()` 委托给 access 模块；路由用 `_authorize_or_403()` 替换 `_row_access()`。
3. `infra/apps/tools.py` 的 `_require_app`、`_may_see_pii`、`integration_ops.py` 的 rank 判定全部改为 `authorize()`。
4. 新增 `APP_ROUTE_ACTIONS` 注册表：每个 `(method, path)` 对应一个 action；测试遍历 FastAPI 路由，缺映射即失败。
5. `participate` 不加入 `PERMISSION_RANK`；未知角色在 `authorize()` 里 fail closed。

### 4.8 配额与成本

- P1 只做两件事：guest 默认关闭 `ai`；沿用现有 lifetime token quota（`users/resource_policy.assert_token_quota_available`）与公开接口限流。
- `export` / `aggregate` / `service_call` / `emit_event` 对 `participate` 默认拒绝，因此不需要新的配额系统。
- per-member / per-app 配额是独立工作项，不在本方案承诺范围内；在它落地前，不把“配额”写进验收标准。

---

## 5. MiniApp 用户管理

管理面放在 Dashboard 的应用详情页，**不放进 MiniApp 沙箱**；用户管理涉及身份、凭证、配额，属于平台基础设施（`docs/miniapp-desktop-system-apps-plan.md:130`）。

### 5.1 成员与访问

- 成员列表：平台成员 / guest / 待审批；角色、来源、状态、最后活跃、创建行数。
- 邀请：用户名（member）/ 邀请链接（外部人）/ 加入码 / 二维码；可设角色、TTL、次数、是否需要审批。
- 表级可见性：逐表勾选 `member_visible`；默认关闭。
- 批量：CSV 导入、搜索、分页、导出成员、批量移除。
- guest 门户 `/my`：列出已加入应用、重新进入、退出 / 删除。

### 5.2 应用级策略

- `access_policy`：`private` / `invite` / `public_read`。
  - `private`：只能由 owner 直接添加成员；不生成加入码。
  - `invite`：允许加入码 / 邀请链接 / IM 交付。
  - `public_read`：保留公开只读链接；guest 仍需邀请才能写。
- 加入码 / 邀请的 `audience` 为 `guest` 或 `member`；`guest` 的 `permission` 只能是 `participate`，`member` 可选 `view` / `participate`（更高角色仍走成员 API）。
- 默认角色、成员上限（member 50 / guest 按 agent policy）、加入码 TTL / 次数、是否需要审批。
- guest 默认关闭 `ai`；`export` / `aggregate` 对 `participate` 固定拒绝（P3 再评估按成员开启）。

### 5.3 Host policy

agent 级 `external_sharing_policy`（`agents` 表 JSON 列）：

```json
{
  "allow_external_participants": false,
  "max_guests_per_app": 50,
  "require_host_approval": true
}
```

- **默认关闭**外部参与；需要 agent owner 显式开启。空 JSON `{}` 按本节默认值解析（关闭 / 50 / 需审批）。
- 适用对象：guest，以及不在该 agent 访问范围内的 member（`not is_agent_accessible(agent_id, user_id)`）。
- app owner = agent owner，或成员已在 agent 访问范围内：不需要 host 审批。
- 否则：默认需要 host 审批；host 可设 guest 上限或关闭外部共享。
- 审批记录落在 `app_member_invites.host_status` 或成员关系的 `pending` 状态；成员页展示 host 策略与被拒绝原因。
- API：`GET/PATCH /api/agents/{id}/external-sharing`。

### 5.4 审批流

| 场景 | 记录 | 审批人 |
|---|---|---|
| 平台 member 申请访问（已有账号） | `app_access_requests`（不改 schema） | `view` / `participate` 只需 app owner；`edit_data` 及以上或需要 Agent 聊天保留双 owner |
| owner 邀请外部人（还没有账号） | `app_member_invites` | app owner；host 策略要求时加 host 审批 |
| 外部人凭加入码自助加入 | `light_app_collaborators.status='pending'` | app owner；host 策略要求时加 host 审批 |
| 加入码 `requires_approval=0` 且 host 不要求 | 直接 `active` | 无需审批 |

- `app_access_requests` **不加 `kind`**。外部人没有 `requester_user_id`，用邀请表表达。
- join 路径不碰 `agents.is_shared`；access 路径保留双 owner。
- 加入 / 邀请兑换按 IP + code 限流；preview 不消费；redeem 原子消费。

### 5.5 审计与同意

- `light_app.member.join / approve / role / remove`、`light_app.invite.create / revoke`、`light_app.join_code.create / revoke / redeem`、`light_app.channel_identity.bind / unbind`、`light_app.host.approve / reject` 写 `audit_log`。
- 加入页展示数据处理说明与同意勾选；guest 提供删除 / 导出入口。
- 删除 guest：`disabled=1` + `status='removed'` + `display_name` 匿名化；行归属保留；硬删除需显式操作并处理 backup / audit / trajectory。

### 5.6 一次性提交（P3）

- `app_submit_tokens`：`token_hash`、`app_id`、`table_key`、`expires_at`、`max_uses`、`used_count`、`revoked_at`、`submitter_mode`（`anonymous` / `email_hash` / `phone_hash`）、`created_by`。
- 不建 `users` 行；行写入时 `row_owner_user_id=NULL`、`submitter_ref` 记录可选哈希。
- 提交成功返回 `receipt_token`（签名，绑定 app + table + row_id + TTL）。`GET /api/public/apps/{token}/submissions/{receipt}` 让提交者看到自己那一条，不需要账号。
- 公开写入只此一条路径；其余公开路由保持只读。

---

## 6. Octop 用户接入

### 6.1 四条路径

1. **Dashboard 应用库**：`list_for_user` 返回 owned + collaborator + agent owner 可见；新增“我参与的”，列出 `participate` 成员的应用。
2. **深链 `/apps/{app_id}`**：平台 JWT + 应用 ACL + 表可见性；无权限时展示权限卡片并发起 `app_access_requests`。
3. **邀请 / 按用户名添加**：应用 owner 把平台用户加为成员；跨 owner / 外部参与者先过 host policy。
4. **申请审批**：`view` / `participate` 只需 app owner（+ host 如需）；`edit_data` 及以上或需要 Agent 聊天，保留双 owner 流程。

### 6.2 两个解耦

- **应用 ≠ Agent**：app-scoped JWT 只允许 `/api/apps/{app_id}` 子树和少量只读启动接口；访问 `/api/agents` 直接 `APP_SCOPE_FORBIDDEN`（`src/octop/api/deps.py:121-190`）。外部参与者不需要被共享 Agent。
- **共享 Agent ≠ 共享应用**：`agents.is_shared` 只影响聊天；应用访问仍走成员关系 + 表可见性。agent owner 的 host 只读权限保留，但不自动推导给所有人。

### 6.3 授权申请

- `view` / `participate`：app owner 单独批准即可，只写 `light_app_collaborators`，**不设置 `is_shared`**。
- `edit_data` 及以上，或需要与 Agent 聊天：保留双 owner 流程（`resolve_access_request` 现有逻辑）。
- 拒绝不回滚、owner 快照、无 rate limit 是既有缺陷；本次只给 join / invite 路径加限流，access 路径单独立项。

### 6.4 member 的 app 会话

- 新增 `POST /api/apps/{app_id}/session`：平台 JWT + 实时 ACL → 2h app token。用于深链 / 前端按应用隔离 token。
- 成员不需要 guest session；app token 过期后用平台 JWT 重新铸造。

---

## 7. IM 渠道接入

### 7.1 身份键

现有：

```
channel_identities(channel_type, external_subject_id)
```

问题：`external_subject_id` 在群聊 / 话题私聊里是会话 id，不是人；飞书话题私聊的 subject 是 `thread_id`，不是 `open_id`。

目标（SQLite 重建表，PG 重命名列）：

```
channel_identities(
  channel_type, account_key, sender_id, user_id,
  verified_at, created_at
)
UNIQUE(channel_type, account_key, sender_id)
```

- `account_key`：稳定外部账号，不用内部 channel row id。
- `sender_id`：稳定人员身份；飞书优先 `union_id`，否则 `open_id`（per-bot）。
- `channel_subject` 只用于回消息路由，不再承担身份。
- 旧数据 `account_key=''` 作为全局回退；解析顺序：**精确 `(channel_type, account_key, sender_id)` 优先，未命中再查 `(channel_type, '', sender_id)`**；新绑定不允许写 `''`。若 legacy 行属于另一个用户，拒绝覆盖，提示先解绑。

| 渠道 | `account_key` | `sender_id` |
|---|---|---|
| 飞书 / Lark | app_id | union_id / open_id |
| 企业微信 | corp_id + agentid | userid |
| 钉钉 | corp_id + app key | userid |
| Telegram | bot id | user id |
| 微信公众号 | appid | openid |
| QQ | appid | openid |

### 7.2 适配器契约与 ToolScope

- 入站必须带 `channel_id / account_key`、`channel_subject`、`metadata.sender_id / sender_name`；缺少的渠道先补适配器，再启用 P2a。
- 持久化 `sender_id / sender_name`；HITL 恢复时用 `ToolScope` 还原。
- `ToolScope` 扩展：

```python
@dataclass(frozen=True)
class ToolScope:
    agent_id: str
    user_id: int              # 会话归属者（P2a 仍是 agent owner）
    source: str
    session_key: str
    chat_type: str
    identity_verified: bool   # 兼容字段：DM 且 sender 已绑定
    owner_verified: bool      # sender_user_id == agent.user_id
    sender_user_id: int | None
    sender_id: str
    account_key: str
```

- `identity_verified` 不再等于“sender 是 owner”；新增 `owner_verified = sender_user_id == agent.user_id`。P2a 的 app 数据工具用 `owner_verified`，链接工具用 `sender_user_id`。

### 7.3 链接与会话

| 链接 / 会话 | 适用场景 | 身份 / 动作 | 时效 | 能做什么 |
|---|---|---|---|---|
| 个人入口码 | owner / participant 私聊 | 已有主体；不创建新身份；`app_access_codes` 增加 `account_key` / `sender_id`，签发时记录 | 10 分钟单次 | app token，按角色 |
| 加入码 | 未绑定私聊 / 定向邀请 | 消费 code；按需建 guest 或复用 member，写成员关系 | 按配置 | 加入应用；进入 WebView |
| 邀请链接 | owner 定向邀请（可先过 host 审批） | 消费 invite；建 guest / member | 按配置 | 加入应用 |
| 公开链接 | 群聊 / 对外公示 | 匿名；不产生身份 | 撤销 / 过期前 | 只读 `public_visible` 表 |
| submission token | 公开表单（P3） | 一次性提交主体 | 按配置 | 单表提交；不建 guest |
| `/link` 握手码 | WebView → IM 身份绑定 | 已登录 guest / member 把 6–8 位码发回私聊 | 5 分钟单次 | 把 IM sender 绑定到该主体 |

流程：

```
DM 需求 → 解析 sender（account_key + sender_id）
→ 个人码 / 加入码 / 公开链接
→ WebView 确认（app token；guest 另发 guest token）
→ 若为 guest 首次进入：页面显示 /link 握手码
→ 用户在 DM 发送 /link <code> → 网关绑定 sender
→ 之后该 guest 可拿个人入口码
```

`/link` 解决的是“浏览器里的人”和“IM 里的人”不是同一个通道的问题；没有它，join code 兑换无法自动建立 IM 绑定，P2a 的“已绑定 participant 私聊拿个人入口”就不成立。

### 7.4 P2a：链接交付 + 工具面收口

- `channel_identities.account_key + sender_id` 与适配器契约先完成。
- `app_share_link` 按 sender 选择：
  - 群聊 → 公开链接，**不发**个人码 / 多用途加入码。
  - 私聊 + 已绑定 member/guest 且是应用成员 → 个人入口码（记录绑定渠道；10 分钟单次）。
  - 私聊 + 未绑定 → 加入码 / 邀请链接（受 host policy 与 `access_policy` 约束）。
  - 私聊 + 已绑定但不是成员 → 加入码 / 申请入口，不发个人码。
- **所有读写应用数据的工具**（`app_read_data`、`app_append_row`、`app_update_row`、`app_delete_row`、`app_import_rows`、aggregate/export 类工具）在 P2a 要求 `owner_verified`；否则拒绝并回复“请打开应用链接”。只放行 `app_share_link` / `app_open` 这类交付工具。
- chat 不返回个人行；个人数据只在 WebView 内展示。
- 部署前置检查（public HTTPS / WebView 白名单）写进 P2a 验收。

### 7.5 P2b：per-sender actor

前置：

- per-sender thread / memory 隔离 / HITL 恢复。
- 群聊仍禁止个人应用工具。

交付：

- `ToolScope.sender_user_id` 成为 app 工具的 actor；`user_id` 仍是会话归属者。
- 审计同时记录 actor 与 session owner。
- HITL 恢复后 actor 不丢。

验收：

- guest 私聊数据不落到 agent owner 的 Dashboard / 记忆。
- 移除成员 / 禁用后下一次请求 403。

### 7.6 群聊硬边界

- 只发公开链接；不发个人入口码，也不发多用途加入链接。
- 不回答个人数据查询；个人数据只在私聊或 WebView 返回。
- `sender_id` 只用于归属与展示，不作为可信身份；可信身份来自 `channel_identities` 绑定 + DM。
- 个人操作要求：已绑定 + 私聊 + 应用成员。

### 7.7 渠道矩阵与部署前置

| 渠道 | 身份 | 卡片 / 按钮 | WebView | 建议 |
|---|---|---|---|---|
| 飞书 / Lark | union_id / open_id + OAuth | ✓ | ✓ | 卡片 + 认证链接，首选 |
| 企业微信 / 微信 | openid + 公众号 OAuth | 有限 | ✓ H5 | 认证链接 + 只读回退 |
| 钉钉 | userid + OAuth | ✓ | ✓ | 同飞书 |
| Telegram | user id + `/bind` | ✓ | 外部浏览器 | 认证链接 + 加入码 |
| QQ | openid | 有限 | 有限 | 公开链接 + 私聊 join |

部署前置：

- 公网 HTTPS、反向代理、WebView 域名白名单、OAuth 回调域名。
- 没有 `public_base_url` / 外网可达性，只支持平台用户与内网访问。
- 企业微信 / 微信还需要公众号配置、域名备案、JS-SDK 域名等，按实际环境确认。

---

## 8. 数据模型与迁移

| 表 | 变化 | 作用 | 阶段 |
|---|---|---|---|
| `users` | 新增 `kind TEXT NOT NULL DEFAULT 'member'`；guest 行无密码 / 邮箱，`role='user'`，`permissions=[]` | guest 的第一版实现；登录 / 权限 / Agent / 列表 / 计数按 member 过滤 | P0 |
| `light_app_tables` | 新增 `member_visible INTEGER NOT NULL DEFAULT 0` | `participate` 能看哪张表；默认关闭 | P0 |
| `light_app_rows` | 新增 `row_owner_user_id INTEGER`；回填 `= created_by`；索引 `(app_id, table_key, row_owner_user_id)` | 行归属与执行者分离 | P0 |
| `light_app_rows` | P3 新增 `submitter_ref TEXT` | 一次性提交的可选身份哈希 | P3 |
| `light_app_collaborators` | 新增 `status TEXT NOT NULL DEFAULT 'active'`、`joined_via TEXT NOT NULL DEFAULT 'share'`、`invited_by INTEGER`；guest 只允许 `participate` | 成员关系、待审批、来源 | P1 |
| `app_join_codes`（新） | `code_hash` UNIQUE、`app_id`、`audience`、`permission`、`expires_at`、`max_uses`、`used_count`、`requires_approval`、`revoked_at`、`created_by`、`created_at`、`bound_channel_type / account_key / sender_id` | 可转发加入码；原子消费、撤销、次数上限 | P1 |
| `app_member_invites`（新） | `invite_id`、`app_id`、`token_hash`、`audience`、`permission`、`invitee_hint`、`expires_at`、`max_uses`、`used_count`、`host_status`、`created_by`、`revoked_at`、`created_at` | owner 定向邀请还没有账号的人；host 审批记录 | P1 |
| `app_sessions`（新） | `session_id`、`user_id`、`scope='guest'`、`refresh_token_hash` UNIQUE、`created_at`、`expires_at`、`revoked_at`、`last_seen_at` | guest 门户会话 / refresh / 撤销 | P1 |
| `agents` | 新增 `external_sharing_policy TEXT NOT NULL DEFAULT '{}'` | host 闸门：外部参与者是否允许进入该 agent 的 workspace | P1 |
| `channel_identities` | 重建：`external_subject_id` → `sender_id`，新增 `account_key TEXT NOT NULL DEFAULT ''`；唯一键 `(channel_type, account_key, sender_id)`；旧行 `account_key=''` | IM 账号 → 主体；解决飞书话题 / 多 bot 串身份 | P2a |
| `app_access_codes` | 新增 `account_key TEXT NOT NULL DEFAULT ''`、`sender_id TEXT NOT NULL DEFAULT ''` | 个人入口码记录签发时的 IM 来源；IM 内兑换可校验，WebView 兑换仍按 bearer 处理 | P2a |
| `channel_link_codes`（新） | `code_hash` UNIQUE、`user_id`、`app_id`、`expires_at`、`consumed_at`、`created_at` | WebView → IM 的 `/link` 握手 | P2a |
| `app_submit_tokens`（新） | `token_hash` UNIQUE、`app_id`、`table_key`、`submitter_mode`、`expires_at`、`max_uses`、`used_count`、`revoked_at`、`created_by` | 一次性提交 | P3 |

**不新增**：`app_access_requests.kind`、`users.role='guest'`、`access_policy='org'`。

### 8.1 迁移规则

- 同时提交 `infra/db/migrations/00N_*.sql` 与 `00N_*.pg.sql`。
- 本分支已有 `045`；若 045 尚未发布，把 P0 + P1 的 schema 折入 045；若 P0 发布时 045 已上线，P0 新开 046，P1 再开 047。每次新开号都同步更新 `tests/unit/db/test_db_pool.py:119` 的版本断言（当前为 `v == 45`）。
- `channel_identities` 的 SQLite 唯一键变更必须重建表；`migrate.py` helper 必须幂等，并覆盖“记录版本已跳过该文件”的库。
- `row_owner_user_id` 回填：`UPDATE ... SET row_owner_user_id = created_by WHERE row_owner_user_id IS NULL AND created_by IS NOT NULL`；NULL 行保持 NULL。
- `users.kind` 回填默认 `member`；不需要扫描 guest（P0 之前没有 guest）。
- 不在已发布迁移上改语义；所有新表 / 列按 AGENTS.md §7 的 SQLite/PG 成对规则提交。

### 8.2 约束与索引

- `channel_identities`：`UNIQUE(channel_type, account_key, sender_id)`；索引 `(user_id, channel_type)`。
- `app_join_codes`：`code_hash` 唯一；索引 `(app_id, revoked_at)`。
- `app_member_invites`：`token_hash` 唯一；索引 `(app_id, host_status)`。
- `app_sessions`：`refresh_token_hash` 唯一；索引 `(user_id, revoked_at)`。
- `light_app_rows`：`(app_id, table_key, row_owner_user_id)`。
- `light_app_collaborators`：保留 `UNIQUE(app_id, user_id)`；`status` 不参与唯一键（移除后再加入复用同一行）。

### 8.3 备份 / 恢复

- 应用数据集导出（`export_dataset` / `import_dataset`）带 `row_owner_ref`（pseudonymous：`user:<id>` + `display_name`），不包含凭证、成员关系、channel identity。
- 恢复：`row_owner_user_id` 能按用户名 / `member_ref` 映射到已有 member 时保留；否则归恢复者并在结果里报告；P3 再提供“创建 guest 占位 / 匿名化”选项。
- 成员关系 / channel identity 不进 bundle；恢复后需重新邀请或绑定。
- 现有恢复路径把 `created_by` 设为 `row.owner_user_id`（`service.py:5788`）；v3 同时写 `row_owner_user_id`，避免 guest 行归给恢复者。

---

## 9. API 与前端

### 9.1 API

| Method | Path | 作用 |
|---|---|---|
| GET / POST | `/api/apps/{id}/members` | 成员列表 / 添加平台成员 |
| PATCH / DELETE | `/api/apps/{id}/members/{user_id}` | 改角色 / 移除（`status='removed'`） |
| GET / POST / DELETE | `/api/apps/{id}/invites` | 外部邀请管理（host 审批状态） |
| POST | `/api/apps/invites/preview` · `/redeem` | 只读预览 / 原子兑换 |
| GET / POST / DELETE | `/api/apps/{id}/join-codes` | 加入码管理 |
| POST | `/api/apps/join/preview` · `/redeem` | 只读预览 / 原子兑换（JWT 豁免、限流） |
| POST | `/api/apps/{id}/session` | 平台 JWT + ACL → 2h app token |
| POST | `/api/guest/apps/{id}/session` | guest token + 成员关系 → 2h app token |
| POST | `/api/guest/session/refresh` | guest refresh 轮换 |
| DELETE | `/api/guest/sessions/current` | 退出 guest 会话 |
| GET | `/api/guest/apps` | guest 门户：已加入应用 |
| DELETE | `/api/guest/memberships/{app_id}` | guest 退出应用 |
| DELETE | `/api/guest/account` | guest 删除 / 禁用账号 |
| POST | `/api/apps/{id}/link-code`（P2a） | 生成 `/link` 握手码（guest / 首次加入） |
| PATCH | `/api/apps/{id}/tables/{key}` | 增加 `member_visible` |
| PUT | `/api/apps/{id}/shares` | 替换平台成员，保留 guest 行 |
| GET | `/api/apps?scope=participating` | 我参与的 |
| GET / PATCH | `/api/agents/{id}/external-sharing` | host policy |
| POST | `/api/public/apps/{token}/submit`（P3） | 一次性提交 |

安全要求：

- join / invite 路由 JWT 豁免，按 IP + code 限流；preview 不消费；redeem 原子消费。
- 已有平台登录态优先用原 JWT；不要替换成 app token。
- 所有端点先补授权矩阵，不能只加 `require("view")`。
- 新错误码：`APP_TABLE_NOT_FOUND`、`APP_MEMBER_PENDING`、`APP_HOST_APPROVAL_REQUIRED`、`APP_GUEST_DISABLED`、`GUEST_SCOPE_FORBIDDEN`、`APP_INVITE_INVALID`、`APP_JOIN_CODE_INVALID`、`APP_SESSION_INVALID`。后端 `errors.*` 与前端 `apiErrors.*` 同步，跑 `uv run pytest tests/unit/i18n -q`。
- 新路由补 OpenAPI tag / summary / typed body；`/api/docs` 不出现空卡片。

### 9.2 前端

- `/join` 确认页：应用名、owner、角色、数据处理说明、同意勾选；P2a 起兑换成功后显示 `/link` 握手码（guest）。
- 应用详情 403 CTA：“申请加入 / 使用加入码”，不再只显示空 / 未找到（`dashboard/src/pages/Apps/Detail.tsx:1350-1366` 目前只有 notFound / 空态）。
- 成员与访问页签：成员、表可见性、host policy、邀请 / 加入码、批量操作。
- guest 模式：隐藏 Dashboard 导航 / Agent 入口；per-app token；无平台 token。
- guest 门户 `/my`：已加入应用、重新进入、退出 / 删除。
- 会话时效：平台 JWT 24h（可滑动续期）；app token 2h（不续期，用平台 JWT 或 guest token 重新铸造）；guest-scoped JWT 7d，refresh token 30d 轮换；guest app token 带 `sid`，session 撤销后立即失效。
- 会话存储：
  - 平台 token：沿用现有 `auth_token`（localStorage / sessionStorage）。
  - app token：`sessionStorage` 按 `app_token:<app_id>` 存；第二个应用不覆盖第一个。
  - guest token：沿用“remember me”策略；store 在 localStorage 或 sessionStorage，key `guest_token`。
  - `request.ts` 按请求路径选 token：`/api/apps/{id}` 用该 app 的 token；`/api/guest/*` 用 guest token；其余用平台 token。不得把 guest token 发给平台路由。

---

## 10. 安全不变量

1. **单一授权内核**：HTTP / Agent / IM 都走 `authorize()`；`PERMISSION_RANK` 不出现在数据访问路径。
2. **Principal 隔离**：guest 不能登录 / 持平台 token / 建 Agent / 进用户列表；submission 不建持久主体。
3. **会话作用域**：platform / app / guest 三种 scope 互不升级；app token 只进单应用；per-app 存储不互相覆盖；guest app token 带 `sid`，可被 session 撤销。
4. **表 / 行双重判定**：`participate` 只能读 `member_visible` 表；user-scoped 表只看自己的行；`row_owner ≠ created_by`。
5. **PII 行级**：`view` 始终脱敏；`participate` 自己的行不脱敏、他人行脱敏；公开面永远脱敏；响应带 `masked_pii_rows` / `masked_pii_columns`；不允许请求级 `permission == "view"`。
6. **加入码 / 邀请原子性**：单次 / 次数上限 / 过期 / 撤销在事务内判断；并发兑换只有一个赢家；join / invite 按 IP + code 限流。
7. **渠道绑定不可抢**：同一 `(channel_type, account_key, sender_id)` 已绑定其他用户时拒绝覆盖；legacy `account_key=''` 只做回退、新绑定不写空串；绑定 / 解绑写审计。
8. **IM 边界**：群聊只发公开链接；P2a 不在 chat 返回个人数据，所有 app 数据工具要求 `owner_verified`；个人码单次、10 分钟、签发时记录 sender；guest 的 IM 身份通过 `/link` 握手建立。
9. **Host 闸门**：guest 与不在 agent 访问范围内的 member 默认需要 host 审批；agent owner 可关闭外部参与。
10. **禁用 / 移除即时生效**：成员移除后下一次请求按实时 ACL 403；guest 禁用后 `resolve_token` 拒绝；app token 带 `sid` 时同时检查 session。
11. **公开面无写入**：P1 / P2 的公开 POST 只有只读缓存 service 调用；匿名写入只在 P3 通过 submission token 开放。
12. **成本**：guest 默认关闭 `ai`；per-action 配额是独立工作项，不在本方案承诺范围内。

---

## 11. 既有缺口与范围

| 问题 | 状态 | 本方案处理 |
|---|---|---|
| PII 4 处 `permission == "view"` | 既有代码 | **P0**：改成行级 `can_view_pii` + 响应掩码标记 |
| `PUT /shares` 全量替换 | 既有代码 | **P0**：只替换平台成员，保留 guest 行 |
| `include_all_rows` 只给 owner/admin | 既有语义 | **P0**：写入授权矩阵，明确 edit_data / participate |
| `PERMISSION_RANK` 散落 | 既有代码 | **P0**：迁到 `access.py`，路由 / 工具清零 |
| `users.kind` 横切 | 结构性 | **P0**：schema + 主边界（凭证 scope）+ 守门清单 |
| 专家审批 = 全局 `is_shared` | 既有缺陷 | 单独立项，不在本次 |
| 未绑定 / 群聊以 owner 执行 app 工具 | 既有缺陷 | **P2a**：工具面收口；**P2b** 再做 actor |
| 入口码纯 bearer | 既有设计 | **P2a**：单次 + 10 分钟 + 签发时记录 sender；guest 绑定用 `/link` |
| 公开面 PII 覆盖不完整 | 既有缺陷 | 单独立项；guest 数据默认不进公开表 |
| 访问申请无 rate limit | 既有缺陷 | join / invite 路径先限流；access 路径单独立项 |
| 备份 / 恢复丢 guest 归属 | 既有路径 | **P3**：成员映射 / 匿名化策略 |
| per-action 配额 | 无基础设施 | 单独立项；P1 只做 guest AI 默认关闭 |

**明确不做**：

- 全局 `is_shared` 审批重做。
- 公开面冻结 HTML / 缓存 service / assets 的全量 PII 过滤。
- IM 多用户聊天记忆隔离（P2b 单独评估）。
- 匿名公开写入（P3 用 submission token）。
- 多租户 SaaS 隔离。
- P1 的 cookie 会话。

---

## 12. 实施路线图

> 任务级拆分、文件清单、DDL、API 契约与测试矩阵见 [`docs/miniapp-identity-implementation-plan.md`](./miniapp-identity-implementation-plan.md)。

### P0：身份基座 + 授权内核（不改 UI）

**交付**

- `PrincipalDirectory` + `users.kind` + `User.kind`；guest 解析绕过 `UserManager`。
- platform / app / guest 三种 scope 的解析与 middleware 白名单。
- `infra/apps/access.py`：`Action` / `Decision` / `authorize()`。
- schema：`light_app_tables.member_visible`、`light_app_rows.row_owner_user_id` + 回填 + 索引。
- 修 4 处 PII exact check、`include_all_rows` 语义、`PUT /shares` 保留 guest。
- 把 `api/routers/apps.py`、`infra/apps/tools.py`、`infra/apps/integration_ops.py` 的 rank 判定全部改为 `authorize()`。
- `APP_ROUTE_ACTIONS` 注册表 + 路由枚举测试。
- guest 守门清单 + 单元测试（fixture 直接建 guest 行）。

**验收**

- 授权矩阵测试全绿：角色 × action × 表 visible × row_owner × PII。
- `grep` 检查通过（§4.7 三条命令）。
- `PUT /shares` 保存后 guest 成员仍在。
- 现有 user-scoped 行仍对原创建者可见（`row_owner` 回填回归）。
- `make all` 绿；不改前端行为。

### P1：Web 成员 + 表可见性 + guest

**交付**

- `light_app_collaborators.status / joined_via / invited_by`；guest cap。
- `app_join_codes`、`app_member_invites`、`app_sessions`、`agents.external_sharing_policy`。
- join / invite preview + redeem；guest 创建 / 复用。
- guest token / app token；按 `app_id` 存储；guest 门户 `/my`。
- 成员与访问页签、表可见性、host policy、审计、同意、批量操作。
- `PUT /shares` 只替换平台成员；`PATCH /tables/{key}` 增加 `member_visible`。

**验收**

- 外部用户加入后只看到 `member_visible` 表；user 表只看自己的行。
- 自己的行 PII 可见；共享表 / 他人行脱敏；响应带 `masked_pii_rows`。
- Agent 代填的行（`row_owner=guest`）guest 能看到；移除成员后下一次请求 403。
- 第二个应用不覆盖第一个应用的会话。
- guest 不能登录、不能拿平台 token、不能出现在用户列表。
- `make all` 绿；`cd dashboard && npx tsc -b` 绿；i18n parity 绿。

### P2a：IM 链接交付

**交付**

- `channel_identities` v2（`account_key + sender_id`）+ 适配器契约；`app_access_codes` 增加 `account_key / sender_id`。
- `ToolScope.sender_user_id / sender_id / account_key / owner_verified`。
- `app_share_link` 按 sender 选择个人码 / 加入码 / 公开链接；群聊只发公开链接。
- **所有 app 数据工具**在 P2a 要求 `owner_verified`；只放行链接 / 打开类工具。
- `channel_link_codes` + `/link` slash command（DM only）。
- 部署前置检查（public HTTPS / WebView 白名单）。

**验收**

- 已绑定 participant 私聊拿个人入口；未绑定拿加入码；群聊只拿公开链接。
- 未绑定 / 群聊无法通过 app 数据工具拿到个人行。
- guest 首次 join 后 `/link` 能把 IM sender 绑定到 guest；转发个人码的风险由单次 + 10 分钟 + 审计记录限制，不把它当作强身份凭证。
- 伪造 sender 不提权。

### P2b：per-sender actor

**前置**

- per-sender thread / memory 隔离 / HITL 恢复。
- 群聊仍禁止个人应用工具。

**交付**

- `ToolScope.sender_user_id` 成为 app 工具 actor；`user_id` 仍是会话归属者。
- 审计记录 actor 与 session owner。

**验收**

- guest 私聊数据不落到 agent owner 的 Dashboard / 记忆。
- HITL 恢复后 actor 不丢。

### P3：一次性提交 / 组织化

**交付**

- `app_submit_tokens` + `submitter_ref` + receipt token + `/submit`。
- guest → member 合并、跨渠道身份合并。
- 用户组 / SSO 域共享（替代 `access_policy='org'`）。
- 备份 / 恢复成员映射。
- cookie 会话 spike（真实外部 WebView）。

**验收**

- 一次性表单不建持久 guest；提交者凭 receipt 能看自己的那条。
- guest 升级后成员关系 / 行归属 / 行历史保留。
- 跨主机恢复后 guest 行归属可解释。

---

## 13. 验收清单

功能验收：

- [ ] `participate` 只看到 `member_visible` 的表；user 表只看自己的行；隐藏表 404。
- [ ] 自己的行 PII 可见；共享表 / 他人行 PII 脱敏；`view` 始终脱敏；`masked_pii_rows` 正确。
- [ ] `participate` 不能 export / aggregate / ai；guest 不能拿平台 token。
- [ ] Agent 代填的行（`row_owner=guest`）guest 能看到；恢复后归属不丢。
- [ ] `PUT /shares` 保存后 guest 成员仍在；成员移除 / 禁用后下一次请求 403。
- [ ] guest 会话可在 WebView 重开；第二个应用不覆盖第一个。
- [ ] IM 群聊只拿公开链接；P2a app 数据工具对未绑定 / 群聊拒绝；host policy 阻止未批准的外部邀请。
- [ ] guest `/link` 握手后绑定成功；重复绑定 / 抢绑被拒。

授权内核验收：

- [ ] `grep -rn "PERMISSION_RANK" src/octop/api src/octop/infra/apps/tools.py src/octop/infra/apps/integration_ops.py` 无结果。
- [ ] `grep -n 'permission == "view"' src/octop/api/routers/apps.py` 无结果。
- [ ] `APP_ROUTE_ACTIONS` 覆盖所有 app 路由；新增路由缺映射时测试失败。

测试：

- [ ] 授权矩阵：角色 × action × 表 visible × row_owner × PII。
- [ ] PII：4 处 exact check 回归；own / shared 行对比；掩码标记。
- [ ] 会话：join 重放、refresh 轮换、撤销、多应用隔离、guest 禁用。
- [ ] 成员：`/shares` 保留 guest、邀请 / 加入码原子消费、host 审批。
- [ ] IM：群聊公开 only、未绑定回退、伪造 sender 不提权、`/link` 绑定、HITL actor 不丢。
- [ ] 迁移：SQLite / PG 成对；`tests/unit/db/test_db_pool.py` 版本断言；`migrate.py` 幂等。
- [ ] `uv run pytest tests/unit -x -q`
- [ ] `uv run pytest tests/integration -x -q`
- [ ] `uv run pytest tests/unit/i18n -q`
- [ ] `make all`
- [ ] `cd dashboard && npx tsc -b`

回退验证：新增断言后，手动回退实现必须让断言失败；只跑绿不算验证。

---

## 14. 决策记录

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| 1 | 身份模型 | **PrincipalDirectory + `users.kind` 第一版** | 复用现有整数 user FK；guest 不进平台凭证路径，横切风险可控 |
| 2 | guest 角色 | **只能 `participate`** | `view` 能读全部表并导出，会绕过 `member_visible` |
| 3 | guest 凭证 | **`guest` scope + app token；P1 无 cookie** | 沙箱 opaque origin 不带 cookie；app-scope 白名单又排除了 Path 作用域 |
| 4 | 会话存储 | **按 `app_id` 存 token** | 解决“one MiniApp only”；不依赖 cookie |
| 5 | 外部邀请 | **`app_member_invites`，不改 `app_access_requests`** | 外部人在邀请时还没有 `users` 行 |
| 6 | 表可见性默认值 | **0；owner 显式开启** | 默认安全；表单模板显式开启 |
| 7 | PII | **行级 `can_view_pii` + 响应掩码标记** | 自己的行不脱敏；前端能区分掩码 |
| 8 | IM actor | **P2a 只交付链接 + 工具面收口；P2b 才 actor** | 避免 guest 数据落到 agent owner 的 Dashboard / 记忆 |
| 9 | Host 闸门 | **默认关闭外部参与，需 host 审批** | 避免别人用你的 workspace / 模型额度招待外部用户 |
| 10 | 配额 | **P1 不做 per-action 配额** | 没有基础设施；不把未实现的能力写进验收 |

---

## 15. 开放问题与风险

1. **per-bot vs cross-bot 身份**：飞书 `open_id` 是 per-app；跨 bot 同一个人需要 `union_id` 或平台账号绑定。P2a 先按 per-bot 定义唯一键，P3 再评估合并。
2. **guest 去重**：同一浏览器复用 `guest` token；跨设备 / 跨渠道靠 `/link` 或 P3 合并；没有稳定键时允许重复 guest，但成员列表要去重展示。
3. **数据控制者**：app owner、agent owner、平台管理员三方关系；guest PII 存在 agent owner 的 workspace / backup。需要同意书 / 保留期 / 删除策略。
4. **guest 规模**：1000+ guest 的成员列表 / 搜索 / 分页 / 通知去重需要单独设计。
5. **成本归属**：guest 用 owner 的模型 / 连接器 / 存储；`usage_log.user_id` 是 guest，但账单归 owner，需要显式 `billing_user_id`（P3）。
6. **cookie 未来**：真实外部 WebView（非 iframe）可以再验证 HttpOnly cookie；P1 不承诺。
7. **迁移撞号**：本分支已有 045，上游合并可能冲突；实施前对齐水位，必要时加桥接。
8. **文档与测试漂移**：`docs/miniapp-overview.md` 的 ACL 描述与代码已有出入；实现时以代码为准并回写文档。

---

## 16. 代码依据

| 主题 | 位置 | 用途 |
|---|---|---|
| 主体 / 平台用户 | `src/octop/infra/users/identity.py:9-31`、`src/octop/infra/users/manager.py:104-130`、`:219-237`、`src/octop/api/routers/auth.py`、`src/octop/infra/users/invites.py` | member 模型、无自助注册、邀请 / SSO、缓存边界 |
| 应用 ACL | `src/octop/infra/apps/service.py:1114-1125`、`src/octop/infra/apps/models.py:46`、`:564`、`src/octop/api/routers/apps.py:70`、`src/octop/infra/db/repos/apps.py:1163-1181` | permission_for、50 上限、全量替换 shares、permission 校验 |
| 权限阶梯 | `src/octop/infra/db/repos/apps.py:22-26`、`src/octop/infra/apps/service.py:1127-1138`、`:1487`、`:1636-1639`、`src/octop/infra/apps/tools.py:283-334`、`src/octop/infra/apps/integration_ops.py:128` | 需要迁到 `authorize()` 的判定点 |
| PII / 行作用域 | `src/octop/api/routers/apps.py:2185`、`:3177`、`:3347`、`:3413`（exact view）、`:3178`、`:3282`、`:3314`、`:3419`（include_all_rows）、`src/octop/infra/apps/service.py:1010-1017`、`:3129-3179`、`:5377-5400` | 行级 PII 改造点 |
| 行写入 / 恢复 | `src/octop/infra/db/migrations/028_light_app_rows.sql:13-27`、`src/octop/infra/apps/service.py:4884-4889`、`:5788` | created_by 语义、导入默认归属、恢复 |
| 访问申请 | `src/octop/infra/apps/service.py:1584-1623`、`src/octop/api/routers/app_access.py:251-352`、`src/octop/infra/db/migrations/031_app_access_requests.sql:10`、`migrations/035_app_access_request_owners.sql` | 双 owner、全局 is_shared、requester NOT NULL |
| 凭证 / 前端 | `src/octop/api/deps.py:121-190`、`src/octop/api/routers/app_access_link.py:135-176`、`dashboard/src/api/request.ts:7-22`、`:109-118` | app scope 白名单、单 token、per-app 存储改造 |
| 沙箱 | `src/octop/infra/apps/sandbox.py:248`、`dashboard/src/pages/Apps/Detail.tsx:541-545` | app iframe 无 same-origin；cookie 不可行 |
| 公开面 | `src/octop/api/routers/public_apps.py:117-240`、`src/octop/infra/apps/service.py:6979-7002` | 只读快照、公开 POST 是缓存 service |
| IM 身份 | `src/octop/infra/gateway/process/message_keys.py:79-105`、`src/octop/infra/auth/channel_binding.py:72-90`、`src/octop/infra/gateway/process/processor.py:577-590`、`src/octop/infra/db/migrations/045_app_setup_plans.sql:47-58` | owner 归属、owner-only 绑定、身份表 |
| IM 工具 | `src/octop/infra/apps/tools.py:283-297`、`:520-651`、`src/octop/infra/agents/tool_scope.py:18-72` | HTTP / Agent 授权差异、app_share_link、HITL actor |
| 前端 | `dashboard/src/pages/Apps/Detail.tsx:1350-1366`、`dashboard/src/plugins/toolRenderers/builtin/AppPermissionCard.tsx:129-162` | notFound / 空态、申请入口 |
| 文档边界 | `docs/miniapp-overview.md:63`、`docs/miniapp-platform-roadmap.md:483-490`、`docs/miniapp-desktop-system-apps-plan.md:130` | 非多租户、D4-lite 现状、用户管理不进沙箱 |

> 行号来自 2026-11-21 当前工作树；实现前请以最新代码复核。

---

## 17. v2 → v3 变更摘要

| 主题 | v2 | v3 |
|---|---|---|
| 反思 | 三处边界重画 | **八处问题 + 根因（身份 / 凭证 / 授权混淆）** |
| 身份 | `users.kind` 作为过渡 | **`users.kind` 是第一版实现；guest 不进平台凭证路径** |
| 凭证 | 平台 JWT + app JWT + per-app cookie | **platform / app / guest 三种 scope；P1 无 cookie** |
| guest 角色 | 可持 `view` / `participate` | **只能 `participate`** |
| 授权 | `authorize()` + rank 并存 | **P0 清零 rank 数据访问；route action 注册表** |
| 表可见性 | P1 | **P0 schema** |
| 行归属 | P1 | **P0 schema + 回填** |
| PII | 行级 `can_view_pii` | **行级 + `masked_pii_rows` / `masked_pii_columns`** |
| 外部邀请 | `app_access_requests.kind` | **`app_member_invites` + `collaborators.status`** |
| IM | P2a 链接交付 | **P2a 链接 + 工具面收口 + `/link` 握手** |
| 配额 | per-action 配额 | **P1 不做；单独立项** |
| `org` 策略 | P1 枚举 | **移除；P3 与用户组 / SSO 域一起做** |
| 公开面 | “只有 GET” | **有只读缓存 POST；P1 / P2 仍无写行能力** |
