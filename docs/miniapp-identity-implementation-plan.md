# MiniApp Identity 功能详细实施计划

> Status: implementation plan / draft  
> Date: 2026-11-21  
> Branch: `feature/miniapp-capabilities`  
> Design: [`docs/miniapp-identity-access-plan.md`](./miniapp-identity-access-plan.md)（v3）  
> 本文只回答“怎么做”：任务拆分、文件、DDL、API、测试、顺序、回滚。设计取舍与安全模型见 v3 方案。

---

## 1. 范围与完成定义

### 1.1 本计划覆盖

- **P0**：身份基座（`users.kind`、`PrincipalDirectory`、三种 token scope）+ 授权内核（`authorize()`）+ 行归属 / 表可见性 schema + 行级 PII + `/shares` 保留 guest。
- **P1**：Web 端成员、邀请、加入码、guest 会话、guest 门户、host policy、成员管理 UI。
- **P2a**：IM 身份键 v2、`ToolScope` sender 字段、`app_share_link` 分流、app 数据工具收口、`/link` 握手。
- **P2b / P3**：只给前置条件和拆分建议，不在本次实施。

### 1.2 完成定义（每个 PR 都适用）

1. `make all` 绿（format-all + lint + typecheck + test）。
2. 新增行为有断言；关键守卫做**回退验证**（临时回退实现，测试必须失败）。
3. 新增 API 有 OpenAPI `summary` / typed body / typed response，`/api/docs` 可读。
4. 新错误码同时更新 `src/octop/i18n/{en,zh}.json` 与 `dashboard/src/locales/{en,zh}.json` 的 `apiErrors`，`uv run pytest tests/unit/i18n -q` 绿。
5. 涉及 `dashboard/` 时，`cd dashboard && npx tsc -b` 绿；必要时跑对应 vitest。
6. 跨平台：测试使用 `tmp_path` / `Path`，POSIX-only 行为加 `posix_only` 标记。
7. 文档回写：设计文档 §12 的路线图或本计划的任务状态同步更新。

### 1.3 明确不在本次实施

- cookie 会话（真实外部 WebView 的 P3 实验）。
- per-action 配额（export / aggregate / service / events 的 per-member 配额）。
- 全局 `agents.is_shared` 审批重做。
- 公开面冻结 HTML / 缓存 service / assets 的 PII 全量过滤。
- 多租户隔离。
- P2b 的 per-sender thread / memory 隔离（单独设计）。

---

## 2. 基线与约束

### 2.1 当前代码锚点

| 主题 | 位置 | 现状 |
|---|---|---|
| User / Role | `src/octop/infra/users/identity.py:9-31` | `User` 无 `kind`；`Role` 只有 admin/user |
| UserRow | `src/octop/infra/db/repos/users.py:39-88` | 无 `kind` |
| UserManager | `src/octop/infra/users/manager.py:104-243` | boot 加载全部用户；`get_by_id` 只扫内存；`count` 统计全部 |
| Token 解析 | `src/octop/api/deps.py:96-190` | 只读 header/query；`scope=app` 白名单；无 guest scope |
| JWT middleware | `src/octop/api/middleware/jwt_auth.py` | 所有 `/api/*` 除白名单 |
| App ACL | `src/octop/infra/apps/service.py:1114-1138` | `permission_for` + `PERMISSION_RANK` |
| 路由 | `src/octop/api/routers/apps.py` | 86 条路由，34 处 `_row_access`（`:912`） |
| Agent 工具 | `src/octop/infra/apps/tools.py:283-297`、`:331-334` | `_require_app` 按 rank；`_may_see_pii` |
| 集成操作 | `src/octop/infra/apps/integration_ops.py:128` | 按 rank 判断 |
| 表 / 行 repo | `src/octop/infra/db/repos/apps.py` | `LightAppTableRow`、`LightAppDataRow`、`create_table`、`append_data_rows` |
| 行过滤 | `src/octop/infra/apps/service.py:2669-2683` | `_row_owner_filter` 返回 viewer id，SQL 按 `created_by` 过滤 |
| PII | `src/octop/api/routers/apps.py:2185/3177/3347/3413`；`service.py:1010-1017` | 请求级 bool；统一 `_apply_pii` |
| 分享 | `service.py:8770-8831`；`repos/apps.py:1163-1181` | `PUT /shares` 全量替换 |
| 访问码 | `src/octop/infra/db/repos/app_access_codes.py`；`api/routers/app_access_link.py` | `channel_type` / `channel_subject_id`，无 sender |
| IM 身份 | `src/octop/infra/db/repos/channel_identities.py` | `(channel_type, external_subject_id)` |
| 前端 token | `dashboard/src/api/request.ts:7-22`、`:109-118` | 单 `auth_token` + app scope marker |

### 2.2 不可违反的约束

- `infra/` 不得 import `api/` / `cli/` / `launch.py`。
- `api/routers/` 只做校验、调用 `infra/`、映射错误；授权逻辑放 `infra/apps/access.py`。
- 所有 workspace 内容读写走 `HarnessAgent.workspace`；本计划不涉及 workspace 内容。
- 迁移必须 `.sql` + `.pg.sql` 成对；SQLite 重建放 `migrate.py` 幂等 helper。
- 不直接编辑 `src/octop/dashboard/`；前端改 `dashboard/` 后 `make build-frontend`。

### 2.3 迁移水位决策（开工第一件事）

当前分支已有 `045_app_setup_plans`，测试断言 `v == 45`，且本分支可能已部署到本地 `~/.octop`。**P0 默认新开 `046`**，P1 `047`，P2a `048`；不要折入 045，否则已到 v45 的库会跳过新列。

开工时执行：

```bash
grep -n "assert v ==" tests/unit/db/test_db_pool.py
sqlite3 ~/.octop/octop.db "SELECT version FROM _schema_version" 2>/dev/null || true
```

若确认所有目标环境都还在 v44 且 045 未发布，才允许把 P0 折入 045；否则按 046/047/048。每次新开号同步更新测试断言与 `AGENTS.md` §7 的版本说明（如仍写 44，一并修正为最新值）。

---

## 3. PR 拆分与依赖

建议按下面 12 个 PR 落地；每个 PR 可独立 review / 回滚。P0 内部有顺序，P1/P2a 可以在 P0 稳定后并行。

| PR | 标题 | 内容 | 依赖 | 规模 |
|---|---|---|---|---|
| PR-01 | `feat(identity): add user kind and visibility schema` | `users.kind`、`light_app_tables.member_visible`、`light_app_rows.row_owner_user_id` + 回填；repo 模型只读字段 | — | M |
| PR-02 | `feat(identity): add principals and token scopes` | `User.kind`、`UserManager` 过滤、`PrincipalDirectory`、platform/app/guest scope | PR-01 | L |
| PR-03 | `feat(identity): add app authorization kernel` | `infra/apps/access.py`、`Action`/`Decision`、`APP_ROUTE_ACTIONS`、路由注册测试 | PR-02 | L |
| PR-04 | `refactor(apps): route data access through authorize()` | `apps.py` 34 处 `_row_access` 迁移；清 rank；PII 临时用 `decision.pii_policy` 布尔 | PR-03 | L |
| PR-05 | `refactor(apps): route tools and integrations through authorize()` | `tools.py`、`integration_ops.py`、`/shares` 保留 guest | PR-04 | M |
| PR-06 | `feat(apps): row-level PII and row ownership` | `can_view_pii`、`PiiPolicy`、`masked_pii_rows`、4 处 PII exact check、写入路径 owner | PR-01/05 | L |
| PR-07 | `feat(apps): add members, invites, and join codes` | `collaborators.status`、`app_member_invites`、`app_join_codes`、成员服务/API | PR-06 | L |
| PR-08 | `feat(apps): add guest sessions and portal` | `app_sessions`、guest/app token、join redeem、`/api/guest/*` | PR-07 | L |
| PR-09 | `feat(apps): add host policy and access-request branch` | `agents.external_sharing_policy`、view/participate 单 owner 审批 | PR-07 | M |
| PR-10 | `feat(dashboard): add members, join, and guest mode` | 成员页签、表可见性、`/join`、`/my`、per-app token、403 CTA | PR-08/09 | L |
| PR-11 | `feat(im): add account_key and sender identity` | `channel_identities` v2、适配器、`ToolScope`、app 数据工具收口 | PR-05 | L |
| PR-12 | `feat(im): add share-link routing and /link handshake` | `app_share_link` 分流、`channel_link_codes`、`/link` | PR-11 | M |

> 规模：S ≈ 0.5–1 天，M ≈ 1–3 天，L ≈ 3–6 天（单人、熟悉代码的粗估）。P2b / P3 不计入。

---

## 4. 详细工作分解

### WP0：开工准备

#### T0.1 基线与迁移决策

- 运行 `make install-hooks`、`make all`，记录基线。
- 执行 §2.3 的版本探测，确定 046/047/048。
- 在 `CHANGELOG.md` 建一个 “MiniApp identity” 小节，按 PR 追加。
- **验收**：基线绿；迁移号写入本计划与设计文档。

#### T0.2 测试夹具

新增 `tests/support/identity.py`（或在现有 `tests/support/fakes.py` 扩展）：

- `make_user(db, *, kind="member", role="user", ...) -> UserRow`
- `make_guest(db, app_id, permission="participate", status="active") -> UserRow`
- `make_app(db, owner_user_id, agent_id, *, tables=...)`
- `make_member(db, app_id, user_id, role, status)`
- `guest_token(server, user_id, session_id=None) -> str`
- `app_token(server, user_id, app_id, kind="member", sid=None) -> str`

**验收**：夹具可在 unit / integration 复用；不引入生产代码依赖。

---

### WP1：身份与凭证（P0）

#### T1.1 `users.kind` 迁移（PR-01）

**文件**

- `src/octop/infra/db/migrations/046_miniapp_identity.sql` + `.pg.sql`
- `tests/unit/db/test_db_pool.py`（版本断言）
- `tests/unit/db/test_miniapp_identity_migration.py`（新）

**DDL**

```sql
ALTER TABLE users ADD COLUMN kind TEXT NOT NULL DEFAULT 'member';
CREATE INDEX IF NOT EXISTS idx_users_kind ON users(kind);

ALTER TABLE light_app_tables ADD COLUMN member_visible INTEGER NOT NULL DEFAULT 0;
ALTER TABLE light_app_rows ADD COLUMN row_owner_user_id INTEGER;
UPDATE light_app_rows
SET row_owner_user_id = created_by
WHERE row_owner_user_id IS NULL AND created_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_light_app_rows_owner
  ON light_app_rows(app_id, table_key, row_owner_user_id);
```

PG 版本使用同样的 `ADD COLUMN IF NOT EXISTS`；`idx_*` 用 `CREATE INDEX IF NOT EXISTS`。

**测试**

- 迁移后 `users.kind` 默认 `member`；`row_owner_user_id` 回填等于 `created_by`；NULL 行保持 NULL。
- 幂等：重复 `run_migrations` 不报错。
- 版本断言更新到 46。

**验收**：旧库升级后现有行可见性不变（`row_owner` 回填回归）。

#### T1.2 User / UserRow / UserManager（PR-02）

**文件**

- `src/octop/infra/users/identity.py`
- `src/octop/infra/db/repos/users.py`
- `src/octop/infra/users/manager.py`
- `src/octop/infra/users/permissions.py`
- `tests/unit/users/test_user_kind.py`（新）

**改动**

1. `User` 增加 `kind: str = "member"`；`UserRow` 增加 `kind: str = "member"`，`from_row` 读取（老库无列时默认 member）。
2. `UserManager.boot`：`user_repo.list(include_disabled=False, kind="member")`。
3. `UserManager.list` / `count` 只统计 member；新增 `count_by_kind(kind)` 到 repo，`count_members()` 到 manager。
4. `get_by_id` 保持 member-only；新增 `get_principal_row(user_id)`（或由 `PrincipalDirectory` 直接走 `user_repo.get`），不要改 `get_by_id` 语义。
5. `authenticate`：`row.kind != "member"` 直接 `None`；`password_hash` 为空仍返回 `None`。
6. `resolve_or_create_sso_user`：`get_by_sso` 命中 guest 时拒绝；新建成员前不按 guest 邮箱合并。
7. `invites.redeem`：目标用户若为 guest，拒绝或走 P3 升级流程。
8. 权限守门：`src/octop/infra/users/permissions.py:289` 的 `user_has_permission` 对 `kind != "member"` 直接 false；`src/octop/api/deps.py:258` 的 `require_permission` / `require_admin` 补 guest 拒绝；`is_admin` 对 guest 恒 false。
9. CLI `user list`（`cli/*_cmd.py`）：过滤 member。

**测试**

- boot 不加载 guest；`get_by_id(guest)` 返回 None。
- guest 登录（用户名 / 邮箱 / SSO / LDAP / 邀请码）全部拒绝。
- `count` / `count_by_kind` 正确。
- member 行为不变。

**验收**：guest 行可以存在，但平台侧看不到、登不进、不计入 setup。

#### T1.3 PrincipalDirectory（PR-02）

**文件**

- `src/octop/infra/users/principals.py`（新）
- `src/octop/api/deps.py`（适配）
- `tests/unit/users/test_principals.py`（新）

**接口**

```python
class PrincipalKind(StrEnum):
    MEMBER = "member"
    GUEST = "guest"
    SUBMISSION = "submission"
    ANONYMOUS = "anonymous"

@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    user_id: int | None
    display_name: str
    app_id: str | None = None
    session_id: str | None = None
    submitter_ref: str | None = None

class PrincipalDirectory:
    def __init__(self, repos: RepoBundle, sessions: GuestSessionService | None = None): ...
    def resolve_token(self, token: str, *, method: str, path: str | None) -> Principal: ...
    def resolve_channel(self, channel_type: str, account_key: str, sender_id: str) -> Principal | None: ...
    def get_user(self, user_id: int) -> Principal | None: ...
```

**实现要点**

- `resolve_token` 先 `_decode`，按 `payload["scope"]` 分派：
  - 无 scope / `platform`：`get_by_id` member；不是 member → `AUTH_FAILED`。
  - `app`：`user_repo.get(sub)`；检查 `kind`、`disabled`；guest 额外检查 `app_sessions` 的 `sid` 未撤销 / 未过期；检查 app 成员关系。
  - `guest`：`user_repo.get(sub)` + `kind=guest` + `disabled=0` + `app_sessions[sid]` 有效。
- `resolve_channel`：精确 `(channel_type, account_key, sender_id)`，再回退 `(channel_type, '', sender_id)`；只返回 member/guest。
- `principal_of(user: User) -> Principal` 适配器；HTTP 路由继续用 `User`，需要授权时转换。

**测试**

- 三种 scope 的合法 / 非法 token。
- guest 被禁用 / session 撤销后拒绝。
- 非成员 app token 拒绝。
- legacy `account_key=''` 回退。

**验收**：`authenticate_request` 能返回带 `kind` 的 `User`；guest 不经过 `UserManager` 缓存。

#### T1.4 Token scope 与 middleware（PR-02）

**文件**

- `src/octop/api/deps.py`
- `src/octop/api/middleware/jwt_auth.py`
- `src/octop/api/routers/auth.py`（平台 token 签发处）
- `tests/unit/api/test_token_scopes.py`（新）

**改动**

1. `sign_token` 的 `extra` 支持 `scope` / `app_id` / `kind` / `sid`；平台 token 可不带 scope（向后兼容），或统一 `scope="platform"` 并兼容缺失。
2. `_check_app_scope`：
   - `scope == "app"`；`app_id`、`sub` 必须存在。
   - `kind == "guest"` 时路径白名单收窄：去掉 `/api/preferences`、`/api/notifications`，保留 `/api/auth/me`、`/api/settings/timezone`、`/api/i18n/`、头像。
3. 新增 `_check_guest_scope`：只允许
   - `/api/auth/me`
   - `/api/settings/timezone`
   - `/api/i18n/*`
   - `/api/users/{id}/avatar`
   - `/api/guest/*`
4. `authenticate_request` 返回 `User`；guest 用 `user_repo.get` 构造，不写 `UserManager._users`。
5. `maybe_sliding_renew_token`：`scope in {"app","guest"}` 直接返回 None，不升级。
6. `is_jwt_exempt_path` 增加：
   - `/api/apps/join/preview`
   - `/api/apps/join/redeem`
   - `/api/apps/invites/preview`
   - `/api/apps/invites/redeem`
   - `/api/guest/session/refresh`（用 refresh token 换取新 guest token，此时 guest JWT 可能已过期）
7. 新签发点：
   - `POST /api/apps/{id}/session`（member）
   - `POST /api/guest/apps/{id}/session`（guest）
   - `POST /api/guest/session/refresh`
   - `POST /api/apps/join/redeem`
   - `POST /api/apps/invites/redeem`

**测试**

- 平台 token 不能带 `scope=app` 访问 `/api/agents`；app token 不能访问 `/api/agents`。
- guest token 只能打 `/api/guest/*` 和白名单。
- app token 滑动续期不升级。
- JWT 豁免路径只限 join/invite preview/redeem，且限流生效。

**验收**：guest 无法以任何方式拿到平台 JWT；app token 单应用。

#### T1.5 Guest 守门清单（PR-02）

逐项加检查与测试：

- `UserManager.boot / list / count / get_by_id`
- 登录、密码重置、SSO、LDAP、邀请码兑换
- `require_permission`
- Agent 创建 / 团队 / 专家发布
- 用户管理列表 / 邀请 / 审计展示 / CLI `user list`
- setup 用户数 / 登录前置
- 分享弹窗按用户名添加成员
- 备份 / 恢复（guest 行随 DB；数据集导出不含成员关系）

**验收**：即使有人误发平台 token 给 guest，平台路由也会拒绝（纵深防御）。

---

### WP2：授权内核（P0）

#### T2.1 `infra/apps/access.py`（PR-03）

**文件**

- `src/octop/infra/apps/access.py`（新）
- `src/octop/infra/apps/models.py`（`SharePermission` / `validate_permission` 扩展）
- `tests/unit/apps/test_access.py`（新）

**内容**

1. `Action` StrEnum（设计文档 §4.3）。
2. `Decision(allowed, role, row_scope, pii_policy, reason)`。
3. `authorize(principal, app, action, *, table_key=None, row=None) -> Decision`。
4. `role_for(principal, app, *, is_admin, agent_owner) -> str | None`：
   - admin / app owner → `owner`
   - `light_app_collaborators` active → role
   - agent owner → `view`
   - 其他 → None
5. `member_visible` 检查：`role == "participate"` 且 `table.member_visible != 1` → deny（上层映射 404）。
6. `row_scope`：
   - owner/admin → all
   - `row_scope=user` → own（所有非 owner 角色）
   - `row_scope=app` → all
7. `pii_policy`：
   - owner/admin/edit_* → visible
   - view → masked
   - participate → mask_unless_own
   - public/anonymous → masked
8. `validate_permission` 增加 `participate`；同步更新 `api/routers/apps.py` 的 `SharePermission = Literal[...]` 与 dashboard 分享弹窗；guest 授予非 participate 时抛 `APP_SHARE_INVALID`。

**测试**：纯函数矩阵，不碰 DB。

**验收**：未知角色 / 未知 action fail closed。

#### T2.2 `AppService.authorize`（PR-03）

**文件**

- `src/octop/infra/apps/service.py`
- `tests/unit/apps/test_service_authorize.py`（新）

**改动**

```python
def authorize(
    self,
    principal: Principal,
    row: AppRecord,
    action: Action,
    *,
    table_key: str | None = None,
    row_record: LightAppDataRow | None = None,
) -> Decision:
    ...
```

> `authorize()` 本身只依赖 `AppRecord` / table / row 数据；路由层的 `_authorize()` helper 负责把 `User` 转成 `Principal`、映射 403 / 404。

- 读 `list_tables(row)` 找 table；未注册表返回 deny。
- **P0 保持现有 `permission_for` 查询**（此时还没有 `status` 列）；T4.1 加 `status` 后，把查询改成只认 `status='active'`，并补 pending / removed 的测试。
  ```sql
  -- T4.1 之后的最终形态
  SELECT permission FROM light_app_collaborators
  WHERE app_id = ? AND user_id = ? AND status = 'active'
  ```
- `list_for_user` 保持现有行为；新增 `list_participating(user_id)` 供 `?scope=participating`；`api/routers/apps.py` 的 `GET /api/apps` 增加 `scope` 参数（默认现有列表）。
- `require` 保留给管理排序，但**数据访问路径不再调用**。

#### T2.3 `APP_ROUTE_ACTIONS` 注册表（PR-03）

**文件**

- `src/octop/api/routers/apps.py`
- `tests/unit/api/test_app_route_actions.py`（新）

**内容**

- 定义 `APP_ROUTE_ACTIONS: dict[tuple[str, str], Action]`，key 为 `(method, path)`。
- 测试遍历 `apps.router.routes`（以及 `public_apps` 之外的相关 router），断言每条路由都有映射。
- 新增路由缺映射 → 测试失败。

**验收**：路由新增不会绕过授权矩阵。

#### T2.4 路由迁移（PR-04）

把 `apps.py` 的 34 处 `_row_access` 按端点族分批替换为：

```python
service, row, decision = _authorize(server, user, app_id, Action.VIEW_ROW, table_key=table)
if not decision.allowed:
    raise OctopError.localized(ErrorCode.APP_FORBIDDEN)
```

建议提交顺序：

1. detail / html / files / assets / events
2. rows list / get / create / update / delete
3. export / aggregate / dataset query / import
4. assistant
5. tables / schema / rules / visibility
6. shares / members / publish / delete
7. bindings / sources / schedules / webhooks / ingress
8. games / history

每批：

- 替换 `_row_access`；
- 用 `decision.pii_policy == "masked"` 临时替代 `permission == "view"`（保证行为不变）；
- 补 `Action` 映射；
- 跑该路由族的 integration 测试；
- PR-06 再把临时布尔换成行级判定，并删除 4 处 exact check。

#### T2.5 Agent 工具与集成（PR-05）

**文件**

- `src/octop/infra/apps/tools.py`
- `src/octop/infra/apps/integration_ops.py`
- `tests/unit/apps/test_tools.py`、`test_integration_tools.py`

**改动**

- 新增 `_authorize_tool(service, app, user_id, action, table=None, row=None)`。
- `_require_app` 保留解析 app / agent，但最终判定走 `authorize()`。
- `_may_see_pii` 删除，改用 `Decision.pii_policy`。
- 每个工具声明 action：
  - `app_read_data` → `VIEW_ROW`
  - `app_append_row` → `CREATE_ROW`
  - `app_update_row` → `UPDATE_ROW`
  - `app_delete_row` → `DELETE_ROW`
  - `app_import_rows` → `CREATE_ROW`（user-scoped 需 `write_any` 或 owner 指定）
  - `app_export_*` → `EXPORT`
  - `app_share_link` → `MANAGE_SHARES`
  - `app_open` → `VIEW_APP`
  - `app_list` / `app_*` 管理工具 → 对应 manage action
- `integration_ops.py:128` 的 `>= edit_data` 改为 `authorize(..., Action.MANAGE_INGRESS)`。

**验收**：`grep -rn "PERMISSION_RANK" src/octop/api src/octop/infra/apps/tools.py src/octop/infra/apps/integration_ops.py` 无结果。

#### T2.6 `/shares` 保留 guest（PR-05）

**文件**

- `src/octop/infra/apps/service.py:8770-8831`
- `src/octop/infra/db/repos/apps.py:1163-1181`
- `tests/unit/apps/test_service.py`

**改动**

- `set_shares` 只替换 `kind='member'` 的协作者；保留 `kind='guest'` 的行（active / pending）。
- `resolve_access_request` 内部仍走 `set_collaborators` 全量替换（它传入完整列表），不受影响。
- 移除成员改用 `PATCH/DELETE /members/{user_id}`，不走 `/shares`。

**验收**：旧分享弹窗保存后 guest 仍在；移除成员 API 能删除。

---

### WP3：可见性 / 行归属 / PII（P0）

#### T3.1 Schema 与 repo 模型（PR-01 / PR-06）

> 字段（`member_visible` / `row_owner_user_id`）随 PR-01 先加进 row dataclass，保证 PR-03 的 `authorize()` 能读到；真正的过滤 / 写入行为在 PR-06 落地。

**文件**

- `src/octop/infra/db/repos/apps.py`
- `src/octop/infra/apps/service.py`（`AppTableView` / `LightAppTableRow`）
- `tests/unit/db/test_repo_apps.py`

**改动**

1. `LightAppTableRow` 增加 `member_visible: bool`；`from_row` 兼容老库。
2. `AppTableView` 增加 `member_visible: bool`。
3. `LightAppRepo.create_table` 增加 `member_visible=False`。
4. 新增 `set_table_member_visible(app_id, table_key, *, member_visible: bool)`。
5. `LightAppDataRow` 增加 `row_owner_user_id: int | None`；`from_row` 兼容。
6. `append_data_rows` / `update_data_row` / `delete_data_row` 读写 `row_owner_user_id`。

#### T3.2 Service 读取与写入（PR-06）

**文件**

- `src/octop/infra/apps/service.py`
- `tests/unit/apps/test_row_owner.py`

**改动**

1. `_row_owner_filter` 改为返回 `owner_user_id`（保持签名），SQL 过滤改用 `COALESCE(row_owner_user_id, created_by)`：
   - `list_data_rows(..., created_by=owner)` 内部 SQL 改为 coalesce。
2. `read_rows` / `read_rows_detailed` / `export_rows` / `aggregate_rows` 的 `viewer_user_id` 语义保持；row_scope=user 走 owner filter。
3. `append_row` / `append_row_record`：
   - 新增 `row_owner_user_id: int | None = None` 参数；默认 `created_by` / actor。
   - user-scoped 表且未指定 → app owner。
4. `update_row` / `delete_row`：
   - 比较 `existing_row.row_owner_user_id or existing_row.created_by`。
5. `import_rows`：
   - 新增 `row_owner_user_id` 参数（仅 owner/admin 可显式指定）；默认逻辑保持。
6. 恢复路径：`service.py:5788` 等 `created_by=row.owner_user_id` 同时写 `row_owner_user_id`。
7. `list_tables` / `AppDetail` 暴露 `member_visible`；`GET /apps/{id}` 对 participate 过滤。

**测试**

- 旧行（row_owner 空）仍按 created_by 可见。
- 新行 owner 正确；更新不改变 owner；删除按 owner。
- guest 只能看 / 改自己的 user-scoped 行。
- agent 代填（显式 owner=guest）guest 能看到。

#### T3.3 PII 行级（PR-06）

**文件**

- `src/octop/infra/apps/access.py`
- `src/octop/infra/apps/service.py`
- `src/octop/api/routers/apps.py`
- `src/octop/infra/apps/tools.py`
- `tests/unit/apps/test_pii_policy.py`

**改动**

1. `can_view_pii(principal, decision, table, row) -> bool`：
   - `pii_policy=="visible"` → True
   - `"masked"` → False
   - `"mask_unless_own"` → `row.row_owner_user_id == principal.user_id`（fallback created_by）
2. service 读路径把 `redact_pii: bool` 扩展为 `pii_policy: PiiPolicy | None`：
   - `_apply_pii(schema, rows, redact_pii=...)` 逐行调用 `redact_rows`；保留旧 bool 兼容。
   - `read_rows_detailed` 返回 `masked_row_ids`。
3. 路由：
   - `AppRowsResponse` 增加 `masked_pii_rows: list[str]`。
   - `DatasetQueryOut` 增加 `masked_pii_rows`。
   - 单行接口增加 `masked_pii_columns`。
   - 替换 4 处 `permission == "view"`。
4. Agent 工具 `app_read_data` 使用 `Decision.pii_policy`。
5. 导出：
   - `participate` → `EXPORT` deny；
   - `view` → 整表脱敏；
   - `edit_*` / owner → 现状。
6. 聚合 / 搜索：
   - `participate` 默认 deny aggregate；
   - 任何可能有掩码行的主体，PII 列 filter/sort/group 拒绝（沿用现有保护）。

**测试**

- owner / edit_data / view / participate / public × own / other 行。
- `masked_pii_rows` 与 `pii_columns` 一致。
- 回退验证：移除 `can_view_pii` 的 own-row 分支，测试必须失败。

---

### WP4：成员 / 邀请 / 加入码 / 会话（P1）

#### T4.1 Collaborators 状态（PR-07）

**迁移（047）**

```sql
ALTER TABLE light_app_collaborators ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE light_app_collaborators ADD COLUMN joined_via TEXT NOT NULL DEFAULT 'share';
ALTER TABLE light_app_collaborators ADD COLUMN invited_by INTEGER;
```

**代码**

- `LightAppCollaboratorRow` 增加 `status` / `joined_via` / `invited_by`。
- `permission_for` 改为只认 `status='active'`（P0 暂时没有该列，T4.1 落地时切换）。
- guest 用户名生成 `guest_<ulid>`；`display_name` 限长（100）并做控制字符清洗；禁止用户自选 username。
- `_clean_collaborators`：
  - guest 只能 `participate`；
  - member cap 50；guest cap 从 host policy 读（默认 50）；
  - owner 不能作为 collaborator。
- `set_collaborators` 保留 `status`；`set_shares` 只替换 member。

#### T4.2 成员服务与 API（PR-07）

**新文件**

- `src/octop/infra/apps/members.py`
- `src/octop/api/routers/app_members.py`
- `tests/unit/apps/test_members.py`
- `tests/integration/test_app_members_api.py`

**方法**

```python
class AppMemberService:
    def list(self, app: AppRecord) -> list[MemberView]
    def add_member(self, app, *, username: str, permission: str, actor: int) -> MemberView
    def add_guest(self, app, *, display_name: str, permission="participate", actor: int) -> MemberView
    def update(self, app, *, user_id: int, permission: str | None, status: str | None, actor: int) -> MemberView
    def remove(self, app, *, user_id: int, actor: int) -> None
```

**API**

- `GET /api/apps/{id}/members`
- `POST /api/apps/{id}/members`（`{username|user_id, permission}`）
- `PATCH /api/apps/{id}/members/{user_id}`（`{permission?, status?}`）
- `DELETE /api/apps/{id}/members/{user_id}`

**规则**

- 只有 app owner / admin 可管理；agent owner 只读。
- 移除：`status='removed'`，不硬删。
- 审计：`light_app.member.join / role / remove`。

#### T4.3 外部邀请（PR-07）

**迁移**

```sql
CREATE TABLE app_member_invites (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  invite_id   TEXT NOT NULL UNIQUE,
  app_id      TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  token_hash  TEXT NOT NULL UNIQUE,
  audience    TEXT NOT NULL,
  permission  TEXT NOT NULL,
  invitee_hint TEXT NOT NULL DEFAULT '',
  expires_at  INTEGER NOT NULL,
  max_uses    INTEGER NOT NULL DEFAULT 1,
  used_count  INTEGER NOT NULL DEFAULT 0,
  host_status TEXT NOT NULL DEFAULT 'not_required',
  created_by  INTEGER NOT NULL,
  revoked_at  INTEGER,
  created_at  INTEGER NOT NULL
);
```

**服务**

- `create_invite`：校验 audience/permission；host policy 要求时 `host_status='pending'`。
- `preview_invite`：只读；host pending 时返回 `APP_HOST_APPROVAL_REQUIRED`。
- `redeem_invite`：原子消费；创建 guest / 复用 member；写 collaborator（active 或 pending）。
- `approve_host`：owner 或 agent owner；`pending → approved`。
- API：`PATCH /api/apps/{id}/invites/{invite_id}`（`{"host_status":"approved|rejected"}`）。
- 限流：`public_slot("invite:"+ip)` + `("invite-code:"+hash[:16])`。

#### T4.4 加入码（PR-07）

**迁移**

```sql
CREATE TABLE app_join_codes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  code_hash    TEXT NOT NULL UNIQUE,
  app_id       TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  audience     TEXT NOT NULL,
  permission   TEXT NOT NULL,
  expires_at   INTEGER NOT NULL,
  max_uses     INTEGER NOT NULL DEFAULT 1,
  used_count   INTEGER NOT NULL DEFAULT 0,
  requires_approval INTEGER NOT NULL DEFAULT 0,
  revoked_at   INTEGER,
  created_by   INTEGER NOT NULL,
  created_at   INTEGER NOT NULL,
  bound_channel_type TEXT NOT NULL DEFAULT '',
  bound_account_key  TEXT NOT NULL DEFAULT '',
  bound_sender_id    TEXT NOT NULL DEFAULT ''
);
```

**服务**

- 创建：owner/admin；guest code 强制 `permission='participate'`。
- preview：不消费，返回 app 名 / 角色 / 是否需要审批。
- redeem：单事务：
  ```sql
  UPDATE app_join_codes SET used_count = used_count + 1
  WHERE code_hash = ? AND revoked_at IS NULL AND expires_at > ?
    AND used_count < max_uses
  RETURNING *;
  ```
  然后创建 guest / member、成员关系、session。
- 并发：只有一个赢家；失败返回 `APP_JOIN_CODE_INVALID`。

#### T4.5 Guest 会话与 token（PR-08）

**迁移**

```sql
CREATE TABLE app_sessions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id         TEXT NOT NULL UNIQUE,
  user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  scope              TEXT NOT NULL DEFAULT 'guest',
  refresh_token_hash TEXT NOT NULL UNIQUE,
  created_at         INTEGER NOT NULL,
  expires_at         INTEGER NOT NULL,
  revoked_at         INTEGER,
  last_seen_at       INTEGER NOT NULL DEFAULT 0
);
```

**服务**

- `create_guest_session(user_id) -> (guest_token, refresh_token)`。
- `rotate(refresh_token) -> (guest_token, refresh_token)`：校验 hash、过期、未撤销；轮换。
- `revoke(session_id)`；`revoke_all(user_id)`。
- guest token：`scope="guest"`, `sub`, `sid`, 7d；refresh token：opaque，30d。
- app token：`scope="app"`, `sub`, `app_id`, `kind`, `sid`（guest），2h。

**API**

- `POST /api/apps/{id}/session`（平台 JWT + ACL → member app token，2h）
- `POST /api/guest/apps/{id}/session`
- `POST /api/guest/session/refresh`
- `DELETE /api/guest/sessions/current`
- `GET /api/guest/apps`
- `DELETE /api/guest/memberships/{app_id}`
- `DELETE /api/guest/account`

**规则**

- 每次 mint app token 重新检查成员关系 `status='active'` 与 `users.disabled=0`；pending / removed 返回 `APP_MEMBER_PENDING` / `APP_FORBIDDEN`。
- 成员移除 / 禁用后，下一次请求 403。
- guest 删除：`disabled=1` + `display_name` 匿名化；session 全撤销。

#### T4.6 Host policy（PR-09）

**迁移**

```sql
ALTER TABLE agents ADD COLUMN external_sharing_policy TEXT NOT NULL DEFAULT '{}';
```

**服务**

- 解析 `{}` 为默认：`allow_external_participants=false`, `max_guests_per_app=50`, `require_host_approval=true`。
- `host_gate(app, user_id|guest)`：
  - guest 或 `not is_agent_accessible` → 检查 allow / 审批 / cap。
  - app owner = agent owner 或 agent 可访问 → 直接通过。
- API：`GET/PATCH /api/agents/{id}/external-sharing`。
- 审计：`light_app.host.approve / reject`。

#### T4.7 Access request 分支（PR-09）

- `view` / `participate` 申请：app owner 单独批准，只写 collaborator，不 `set_shared`。
- `edit_data+` / 需要 Agent 聊天：保留现有双 owner 流程。
- join / invite 限流；access 路径限流单独立项。
- 测试：单 owner 批准、双 owner 批准、拒绝不回滚现状。

#### T4.8 Dashboard（PR-10）

**文件**

- `dashboard/src/api/modules/apps.ts`：members / invites / join / guest API。
- `dashboard/src/pages/Apps/Detail.tsx`：成员与访问页签、表可见性、403 CTA。
- `dashboard/src/pages/Apps/MembersPanel.tsx`（新）
- `dashboard/src/pages/Join/index.tsx`（新）
- `dashboard/src/pages/Guest/My.tsx`（新）
- `dashboard/src/api/request.ts`：per-app token 选择。
- `dashboard/src/locales/{en,zh}.json`：新文案。

**token 选择**

```ts
function tokenFor(path: string): string {
  const m = path.match(/^\/api\/apps\/([^/]+)/);
  if (m) return getAppToken(m[1]) || getGuestToken() || getAuthToken();
  if (path.startsWith("/api/guest/")) return getGuestToken();
  return getAuthToken();
}
```

- `auth_token` 保持现状；新增 `app_token:<app_id>`（sessionStorage）与 `guest_token`（按 remember 策略）。
- `setAuthToken` 不再清空 app token（或只清当前 tab 的 marker）。
- 403 CTA：区分 403 / 404；展示“申请加入 / 使用加入码”。

**UI 验收**

- 第二个应用不覆盖第一个应用的 token。
- guest 模式隐藏侧栏 / Agent 入口；`/my` 可重新进入。
- 成员页可逐表开启 `member_visible`；邀请 / 加入码可复制。

---

### WP5：IM 身份与链接（P2a）

#### T5.1 `channel_identities` v2（PR-11）

**迁移（048，SQLite 重建）**

```sql
CREATE TABLE channel_identities_new (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_type  TEXT NOT NULL,
  account_key   TEXT NOT NULL DEFAULT '',
  sender_id     TEXT NOT NULL,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  verified_at   INTEGER NOT NULL,
  created_at    INTEGER NOT NULL,
  UNIQUE(channel_type, account_key, sender_id)
);
INSERT INTO channel_identities_new(
  id, channel_type, account_key, sender_id, user_id, verified_at, created_at
)
SELECT id, channel_type, '', external_subject_id, user_id, verified_at, created_at
FROM channel_identities;
DROP TABLE channel_identities;
ALTER TABLE channel_identities_new RENAME TO channel_identities;
CREATE INDEX idx_channel_identities_user ON channel_identities(user_id, channel_type);
```

PG 用 `ALTER TABLE ... RENAME COLUMN` + 删除旧唯一约束 + 新增唯一约束；`migrate.py` helper 保持幂等。

**Repo**

- `ChannelIdentityRow` 字段改为 `account_key` / `sender_id`。
- `get(channel_type, account_key, sender_id)`：先精确，miss 再 `('', sender_id)`。
- `bind`：精确键插入；若 legacy `''` 行属于其他用户，拒绝。
- `delete` 更新为新键。
- `api/routers/channel_bindings.py`：对外 `subject_id` 字段保持不变（映射 `sender_id`），避免 dashboard 破坏；新增可选 `account_key` 响应字段。`infra/auth/channel_binding.py` 的 `bind_identity` / `is_owner_verified` 增加 `account_key` 参数。

**测试**

- 迁移后旧行 `account_key=''`，仍能命中。
- 精确键优先于 legacy。
- 抢绑拒绝。

#### T5.2 适配器契约（PR-11）

逐个渠道确认入站携带：

- `account_key`：飞书 app_id、企微 corp_id+agentid、钉钉 corp_id+app key、Telegram bot id、公众号 appid、QQ appid。
- `metadata.sender_id` / `sender_name`：优先稳定 id（飞书 union_id → open_id）。
- `channel_subject` 只用于回消息。

**文件**：`src/octop/infra/gateway/process/harness_request.py`、`message_keys.py`、`processor.py`、`octop_gateway` 适配器（外部包；通过版本升级或 adapter 层补齐）。

**测试**

- 每个渠道一条 unit：`resolve_user_id_for_message` 仍归 owner；`sender_id` 正确持久化。
- 缺 `sender_id` 的渠道在 P2a 前标记为“未启用”。

#### T5.3 ToolScope（PR-11）

**文件**

- `src/octop/infra/agents/tool_scope.py`
- `src/octop/infra/gateway/process/processor.py`
- `src/octop/infra/gateway/process/harness_request.py`
- `tests/unit/agents/test_tool_scope.py`

**字段**

```python
identity_verified: bool
owner_verified: bool
sender_user_id: int | None
sender_id: str
account_key: str
```

- `owner_verified = sender_user_id == agent.user_id`。
- `is_owner_verified` / `is_identity_verified` 改为接收 `account_key + sender_id`；`is_owner_verified` 保留为 `verified_user_id == agent.user_id` 的包装，避免其它调用点一次性改爆。
- HITL 恢复时从 session / pending 行恢复这些字段。
- 审计同时记录 `actor` 与 `session_owner`。

#### T5.4 App 数据工具收口（PR-11）

**文件**

- `src/octop/infra/apps/tools.py`
- `tests/unit/apps/test_tools.py`

**改动**

- 新增 `_im_tool_allowed(scope, tool_name)`：
  - P2a：只有 `app_share_link` / `app_open` 允许非 `owner_verified`。
  - 其余读写工具要求 `owner_verified`。
- 群聊：即使 `owner_verified` 也拒绝个人数据工具（`chat_type != dm`）。
- 拒绝信息本地化。

**测试**

- 未绑定 DM → `app_read_data` 拒绝，`app_share_link` 允许。
- 群聊 → 全部数据工具拒绝。
- owner DM → 数据工具允许。

#### T5.5 `app_share_link` 分流（PR-12）

**文件**

- `src/octop/infra/apps/tools.py:520-651`
- `src/octop/infra/apps/service.py:7039-7113`
- `src/octop/infra/db/repos/app_access_codes.py`
- `tests/unit/apps/test_access_links.py`

**规则**

| 场景 | 输出 |
|---|---|
| 群聊 | 公开链接；不生成个人码 / 多用途码 |
| 私聊 + 未绑定 | 加入码 / 邀请链接（受 policy） |
| 私聊 + 已绑定 member/guest + 是成员 | 个人入口码（记录 `account_key` / `sender_id`） |
| 私聊 + 已绑定但不是成员 | 加入码 / 申请入口 |

**迁移（048 的一部分）**

```sql
ALTER TABLE app_access_codes ADD COLUMN account_key TEXT NOT NULL DEFAULT '';
ALTER TABLE app_access_codes ADD COLUMN sender_id TEXT NOT NULL DEFAULT '';
```

**实现**

- `_channel_link_payload` 使用 `scope.sender_user_id` 而不是 `user_id` 决定个人码主体。
- `create_access_link` 增加 `account_key` / `sender_id` 参数并写表。
- **兑换路径支持 guest**：`api/routers/app_access_link.py` 的 `_linked_user` 目前走 `user_manager.get_by_id`（member-only），必须改为 `user_repo.get`；guest 的 exchange 返回 `kind="guest"` 的 app token，并可选返回 `guest_token`（复用 / 新建 `app_sessions`），响应模型 `AppAccessExchangeOut` 加可选 `guest_token`。
- 个人码 TTL 10 分钟、单次；不加 cookie。
- 审计 `light_app.access_link.create` 带 sender。

#### T5.6 `/link` 握手（PR-12）

**迁移**

```sql
CREATE TABLE channel_link_codes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  code_hash   TEXT NOT NULL UNIQUE,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  app_id      TEXT NOT NULL REFERENCES light_apps(app_id) ON DELETE CASCADE,
  expires_at  INTEGER NOT NULL,
  consumed_at INTEGER,
  created_at  INTEGER NOT NULL
);
```

**流程**

1. join redeem / guest 首次进入 → `POST /api/apps/{id}/link-code`（用 **app token**；guest token 的 scope 只开 `/api/guest/*`，不能打 app 子树）→ 6–8 位码，5 分钟。
2. `/join` 页面显示“在收到链接的私聊里发送 `/link <code>`”。
3. 网关 slash handler：
   - 仅 DM；
   - 消费 code；
   - `bind_identity(channel_type, account_key, sender_id, user_id)`；
   - 已绑定其他用户 → 拒绝；写审计。
4. 绑定后，`app_share_link` 可给该 guest 发个人入口码。

**文件**

- `src/octop/infra/gateway/slash/catalog.py`
- `src/octop/infra/gateway/slash/handlers/platform.py`
- `src/octop/infra/auth/channel_binding.py`
- `src/octop/api/routers/app_join.py`（link-code）
- `tests/unit/gateway/test_slash_link.py`

---

### WP6：P2b / P3（只列前置，不实施）

#### P2b：per-sender actor

前置（必须单独设计并评审）：

- per-sender thread key（`threads` / `session_key` 不再只按 agent+channel+subject）。
- memory scope 隔离；HITL 恢复 actor。
- 群聊仍禁止个人数据工具。

交付：`ToolScope.sender_user_id` 成为 app 工具 actor；审计 actor + session owner。

#### P3：一次性提交 / 组织化

- `app_submit_tokens` + `submitter_ref` + receipt token + `/submit`。
- guest → member 合并、跨渠道合并。
- 用户组 / SSO 域共享（替代 `access_policy='org'`）。
- 备份 / 恢复成员映射。
- cookie 会话 spike（真实外部 WebView）。

---

## 5. 迁移与 DDL 汇总

### 5.1 版本规划

| 版本 | 阶段 | 内容 |
|---|---|---|
| 046 | P0 | `users.kind`、`light_app_tables.member_visible`、`light_app_rows.row_owner_user_id` + 回填 + 索引 |
| 047 | P1 | `light_app_collaborators.status/joined_via/invited_by`、`app_join_codes`、`app_member_invites`、`app_sessions`、`agents.external_sharing_policy` |
| 048 | P2a | `channel_identities` 重建、`app_access_codes.account_key/sender_id`、`channel_link_codes` |
| 049 | P3 | `light_app_rows.submitter_ref`、`app_submit_tokens`、用户组 / SSO 域 |

> 若开工时确认 045 未发布且所有环境 < 45，可把 046 折入 045；否则坚持 046+。**不要**在已到 v45 的库上改 045。

### 5.2 SQLite / PG 注意事项

- `ALTER TABLE ADD COLUMN` 带 `NOT NULL DEFAULT` 在两边都可用。
- `channel_identities` 重建：SQLite `PRAGMA foreign_keys=OFF` → 新表 → 拷数据 → 删旧表 → 重命名 → 建索引 → 恢复 FK；必须在事务内，`migrate.py` helper 幂等。
- PG 用 `ALTER TABLE ... RENAME COLUMN`、`DROP CONSTRAINT`、`ADD CONSTRAINT`；旧库的 `UNIQUE(channel_type, external_subject_id)` 名字需先查。
- 所有新表按 AGENTS.md §7 的资源表约定：`id` 整数主键 + `{entity}_id` 公开字符串；child 表存字符串 id。
- `app_member_invites` / `app_join_codes` / `app_sessions` 的公开 id 用 ULID / short id；`code_hash` / `token_hash` 唯一。

### 5.3 回填

- `users.kind`：默认 `member`，无需扫描。
- `row_owner_user_id`：`= created_by`；NULL 保持 NULL，按 app owner 处理。
- `light_app_collaborators.status`：默认 `active`；`joined_via` 默认 `share`。
- `channel_identities.account_key`：旧行 `''`；`sender_id` 取旧 `external_subject_id`。

---

## 6. API 契约草案

### 6.1 成员

```http
GET /api/apps/{app_id}/members
→ {"members":[{"user_id":1,"username":"...","display_name":"...","kind":"member|guest","permission":"participate","status":"active|pending","joined_via":"share|join_code|invite|im","last_active_at":0}]}

POST /api/apps/{app_id}/members
{"username":"alice","permission":"view"}   # member only
→ MemberOut

PATCH /api/apps/{app_id}/members/{user_id}
{"permission":"participate"} | {"status":"removed"}
→ MemberOut

DELETE /api/apps/{app_id}/members/{user_id}
→ 204
```

### 6.2 邀请

```http
POST /api/apps/{app_id}/invites
{"audience":"guest","permission":"participate","invitee_hint":"a@b.com","ttl_seconds":86400,"max_uses":1}
→ {"invite_url":"...","expires_at":...,"host_status":"pending|not_required"}

POST /api/apps/invites/preview   {"token":"..."}
→ {"app_id":"...","app_name":"...","permission":"participate","host_status":"approved"}

POST /api/apps/invites/redeem    {"token":"..."}
→ {"app_id":"...","role":"participate","guest_token":"...","app_token":"..."}
```

### 6.3 加入码

```http
POST /api/apps/{app_id}/join-codes
{"audience":"guest","ttl_seconds":604800,"max_uses":10,"requires_approval":false}
→ {"code":"...","expires_at":...}

POST /api/apps/join/preview  {"code":"..."}
→ {"app_id":"...","app_name":"...","role":"participate","requires_approval":false}

POST /api/apps/join/redeem   {"code":"..."}
→ {"app_id":"...","role":"participate","guest_token":"...","app_token":"..."}
```

### 6.4 Guest 会话

```http
POST /api/guest/apps/{app_id}/session
Authorization: Bearer <guest_token>
→ {"access_token":"...","expires_in":7200,"app_id":"...","role":"participate"}

POST /api/guest/session/refresh
{"refresh_token":"..."}
→ {"guest_token":"...","refresh_token":"..."}

GET /api/guest/apps
→ {"apps":[{"app_id":"...","name":"...","role":"participate","last_opened_at":0}]}

DELETE /api/guest/sessions/current  → 204
DELETE /api/guest/memberships/{app_id} → 204
DELETE /api/guest/account → 204
```

### 6.5 Host policy

```http
GET  /api/agents/{id}/external-sharing
→ {"allow_external_participants":false,"max_guests_per_app":50,"require_host_approval":true}

PATCH /api/agents/{id}/external-sharing
{"allow_external_participants":true,"max_guests_per_app":100,"require_host_approval":true}
```

### 6.6 `/link`

```http
POST /api/apps/{app_id}/link-code
Authorization: Bearer <guest_token|app_token>
→ {"code":"123456","expires_at":...}
```

### 6.7 错误码

新增到 `infra/errors.py` + i18n：

- `APP_TABLE_NOT_FOUND`（隐藏表 404）
- `APP_MEMBER_PENDING`
- `APP_HOST_APPROVAL_REQUIRED`
- `APP_GUEST_DISABLED`
- `GUEST_SCOPE_FORBIDDEN`
- `APP_INVITE_INVALID`
- `APP_JOIN_CODE_INVALID`
- `APP_SESSION_INVALID`

---

## 7. 测试计划

### 7.1 单元

| 文件 | 覆盖 |
|---|---|
| `tests/unit/users/test_user_kind.py` | boot / list / count / login / SSO / 守门 |
| `tests/unit/users/test_principals.py` | token scope、channel 回退、禁用 |
| `tests/unit/api/test_token_scopes.py` | app / guest 白名单、滑动续期 |
| `tests/unit/apps/test_access.py` | `authorize` 纯函数矩阵 |
| `tests/unit/api/test_app_route_actions.py` | 路由 action 注册表完整性 |
| `tests/unit/apps/test_service_authorize.py` | active-only、agent owner fallback、member_visible |
| `tests/unit/apps/test_row_owner.py` | 回填、读写、导入、恢复 |
| `tests/unit/apps/test_pii_policy.py` | own / shared / view / participate / public |
| `tests/unit/apps/test_members.py` | 状态机、caps、guest 角色限制 |
| `tests/unit/apps/test_join_codes.py` | 原子消费、TTL、max_uses、audience |
| `tests/unit/apps/test_invites.py` | host_status、preview / redeem |
| `tests/unit/auth/test_guest_sessions.py` | refresh 轮换、撤销、sid |
| `tests/unit/gateway/test_channel_identity_v2.py` | 精确 / legacy 回退、抢绑 |
| `tests/unit/gateway/test_slash_link.py` | DM only、绑定、重复绑定 |
| `tests/unit/db/test_miniapp_identity_migration.py` | 046/047/048 幂等与回填 |

### 7.2 集成

- `tests/integration/test_app_members_api.py`
- `tests/integration/test_app_join_api.py`
- `tests/integration/test_guest_portal_api.py`
- `tests/integration/test_app_shares_preserve_guest.py`
- `tests/integration/test_app_scope_forbidden.py`
- `tests/integration/test_channel_bindings_api.py`（扩展现有）

### 7.3 前端

- `dashboard/src/api/request.test.ts`：按路径选 token、per-app 不覆盖。
- `dashboard/src/pages/Apps/MembersPanel.test.tsx`：角色 / 表可见性 / 邀请。
- `dashboard/src/pages/Join/Join.test.tsx`：preview / redeem / 同意。
- `dashboard/src/pages/Guest/My.test.tsx`：列表 / 退出。
- `dashboard/src/pages/Login/AppAccessComplete.test.tsx`：guest token 分支。

### 7.4 回退验证（必须逐项做）

| 守卫 | 回退后应失败的测试 |
|---|---|
| `permission_for` 只认 active | pending 成员仍能访问 |
| `member_visible` 检查 | participate 读到隐藏表 |
| `row_owner` 过滤 | guest 看到他人行 |
| `can_view_pii` own-row | view / participate PII 断言 |
| `_check_guest_scope` | guest token 打到平台路由 |
| `_im_tool_allowed` | 未绑定 DM 调 `app_read_data` |
| join code 原子条件 | 并发兑换两个赢家 |

### 7.5 命令

```bash
uv run pytest tests/unit -x -q
uv run pytest tests/integration -x -q
uv run pytest tests/unit/i18n -q
make all
cd dashboard && npx tsc -b
```

---

## 8. 发布、监控与回滚

### 8.1 分阶段开关

- **P0**：暗发布。schema 加列可空 / 有默认；前端无变化；guest 不产生。回滚 = 回退代码，列保留。
- **P1**：按应用生效。只有 `access_policy='invite'` 且 host policy 允许时才能创建 guest / 加入码；默认关闭。
- **P2a**：按渠道生效。缺 `sender_id` 的渠道不启用个人码；群聊永远只发公开链接。

### 8.2 监控

新增 `METRICS` 计数器：

- `miniapp_authorize_denied_total{action, reason}`
- `miniapp_guest_join_total{via}`
- `miniapp_join_code_redeem_total{result}`
- `miniapp_guest_session_refresh_total{result}`
- `miniapp_im_tool_denied_total{tool, reason}`

审计必写：

- `light_app.member.*`
- `light_app.invite.*`
- `light_app.join_code.*`
- `light_app.guest_session.*`
- `light_app.channel_identity.*`
- `light_app.host.*`

### 8.3 回滚

| 阶段 | 回滚方式 |
|---|---|
| P0 | 回退代码即可；046 列保留，不影响旧代码（旧代码不读新列） |
| P1 | 关闭 host policy / `access_policy`；已有 guest 可禁用；047 表保留 |
| P2a | 先 `octop backup`；`channel_identities` 重建不可逆，回滚需恢复 DB；代码回退后旧代码读 `external_subject_id` 会失败，因此**必须先用兼容视图或双写** |

> `channel_identities` 是唯一破坏性迁移。实施前必须：备份 DB、在临时库演练、确认 `migrate.py` 幂等、准备恢复步骤。若无法接受停机，先做双列（`external_subject_id` 保留 + 新列双写），下个版本再删旧列。

---

## 9. 风险与待确认决策

| # | 风险 / 决策 | 建议 | 需要谁确认 |
|---|---|---|---|
| 1 | 046 vs 折入 045 | 默认 046；先探测目标库版本 | 项目 owner |
| 2 | guest 去重 | 同浏览器复用 guest token；跨设备允许重复，列表去重展示 | 产品 |
| 3 | host policy 默认值 | 关闭 / 50 / 需审批 | 产品 |
| 4 | 个人码 WebView 仍是 bearer | 单次 + 10 分钟 + 审计；不承诺强绑定 | 安全 |
| 5 | `channel_identities` 破坏性迁移 | 先备份 + 演练；必要时双写一版 | 运维 |
| 6 | P2a 渠道覆盖范围 | 先飞书 / 钉钉 / Telegram；企微 / 微信 / QQ 后置 | 产品 |
| 7 | 前端 UI 范围 | P1 先做成员 + 表可见性 + `/join`；批量 / CSV 后置 | 产品 |
| 8 | 配额 | P1 不做；guest AI 默认关闭 | 产品 |

---

## 10. 里程碑与并行

| 里程碑 | 内容 | 出口条件 |
|---|---|---|
| M0 | PR-01 ~ PR-06 | P0 完成；`make all` 绿；grep 清零；现有行为回归 |
| M1 | PR-07 ~ PR-10 | P1 完成；guest 加入 / 表可见性 / 门户可用；前端 tsc 绿 |
| M2 | PR-11 ~ PR-12 | P2a 完成；IM 链接分流 / 工具收口 / `/link` 可用 |
| M3 | P2b / P3 | 单独设计评审后启动 |

并行建议：

- PR-01 先合，解锁 schema。
- PR-02 / PR-03 可并行（接口先定）。
- PR-04 / PR-05 / PR-06 串行（依赖 `authorize()`）。
- PR-07 ~ PR-09 可在 P0 稳定后并行；PR-10 依赖 API。
- PR-11 / PR-12 在 P1 API 稳定后并行。

---

## 11. 附录：文件清单

### 11.1 新增文件

```
src/octop/infra/users/principals.py
src/octop/infra/apps/access.py
src/octop/infra/apps/members.py
src/octop/infra/auth/guest_sessions.py
src/octop/api/routers/app_members.py
src/octop/api/routers/app_join.py
src/octop/api/routers/guest.py
src/octop/infra/db/repos/app_join_codes.py
src/octop/infra/db/repos/app_member_invites.py
src/octop/infra/db/repos/app_sessions.py
src/octop/infra/db/repos/channel_link_codes.py
src/octop/infra/db/migrations/046_miniapp_identity.{sql,pg.sql}
src/octop/infra/db/migrations/047_miniapp_members.{sql,pg.sql}
src/octop/infra/db/migrations/048_im_identity_v2.{sql,pg.sql}
dashboard/src/pages/Apps/MembersPanel.tsx
dashboard/src/pages/Join/index.tsx
dashboard/src/pages/Guest/My.tsx
```

### 11.2 主要修改文件

```
src/octop/infra/users/identity.py
src/octop/infra/db/repos/users.py
src/octop/infra/users/manager.py
src/octop/infra/users/permissions.py
src/octop/api/deps.py
src/octop/api/middleware/jwt_auth.py
src/octop/infra/apps/service.py
src/octop/infra/apps/models.py
src/octop/infra/apps/tools.py
src/octop/infra/apps/integration_ops.py
src/octop/infra/apps/wiring.py
src/octop/infra/db/repos/apps.py
src/octop/infra/db/repos/app_access_codes.py
src/octop/infra/db/repos/channel_identities.py
src/octop/infra/auth/channel_binding.py
src/octop/infra/gateway/process/processor.py
src/octop/infra/gateway/process/message_keys.py
src/octop/infra/gateway/slash/catalog.py
src/octop/infra/gateway/slash/handlers/platform.py
src/octop/api/routers/apps.py
src/octop/api/routers/app_access_link.py
src/octop/api/routers/channel_bindings.py
src/octop/api/app.py
dashboard/src/api/request.ts
dashboard/src/api/modules/apps.ts
dashboard/src/pages/Apps/Detail.tsx
dashboard/src/locales/{en,zh}.json
```

### 11.3 开工 checklist

- [ ] 确认迁移水位（046 / 047 / 048）
- [ ] 建 `tests/support/identity.py`
- [ ] PR-01 schema + 回填 + 迁移测试
- [ ] PR-02 User.kind + PrincipalDirectory + scope
- [ ] PR-03 access.py + route registry
- [ ] PR-04 路由迁移 + grep
- [ ] PR-05 工具迁移 + `/shares`
- [ ] PR-06 PII + row_owner
- [ ] PR-07 成员 / 邀请 / 加入码
- [ ] PR-08 guest session / portal
- [ ] PR-09 host policy / access request 分支
- [ ] PR-10 dashboard
- [ ] PR-11 channel identity v2 / tool gate
- [ ] PR-12 share-link / `/link`
- [ ] 回退验证全部通过
- [ ] 文档回写（设计文档 §12 / 本计划状态）
