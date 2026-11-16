# MiniApp 应用级调度 Review 记录

> 日期：2026-11-15
> 分支：`feature/miniapp-capabilities`
> 范围：`light_app_schedules`、调度循环、`sync_source` / `snapshot_publication`、迁移、公开链接弹窗。
> 状态：**2026-11-15 已修复必修项 #1（snapshot 调度权限 + 审计）；其余结论尚未修改代码。**

## 1. 结论

设计方向成立：应用级 `light_app_schedules` 统一了数据源同步与公开快照刷新，DB 租约 + 心跳 + `next_run_at` + 指数退避比旧的 `claim_source` 语义可靠；动作仍走 `AppService`，迁移与接口/UI 也已闭环。

但**不建议按现状合并**，按必要性分四档：**4 项必修、7 项建议本轮、6 类可延后、其余另开单**。判断依据：

1. 安全与数据正确性优先；
2. schema v38 未发布，唯一约束/迁移类问题现在修成本最低；
3. “多实例只执行一次”是本功能明示验收标准，租约问题不能只靠动作幂等兜底。

定向验证（未跑完整 `make all`）：

```text
uv run pytest tests/unit/apps -q                                  -> 364 passed
uv run pytest tests/unit/apps/test_scheduler.py -q               -> 13 passed
uv run pytest tests/unit/db/test_app_schedules_migration.py -q   -> 2 passed
uv run pytest tests/integration/test_apps_api.py -q -k schedule  -> 1 passed
```

## 2. 实现概览

- 表：`light_app_schedules`（schema v38），整数 `id` + 公共 `schedule_id`，`app_id`/`created_by` 级联；索引 `(enabled, next_run_at)`、`app_id`。
- 调度：`AppSourceScheduler.tick()` 扫描 due 行，条件 UPDATE claim 租约，心跳续租，动作硬超时，完成时间重排，确定性抖动，失败指数退避（上限 24h）。
- 动作：`sync_source`（镜像 `light_app_sources.schedule_seconds`，运行结果同时写 source 与 schedule）与 `snapshot_publication`（读取当前 HTML/公开表结构覆盖发布快照）。
- API：`GET/POST /api/apps/{id}/schedules`、`PATCH/DELETE .../{schedule_id}`。
- UI：公开链接弹窗开关自动刷新（1h/6h/1d），展示下次运行与上次错误；数据源面板仍用 `schedule_seconds` 选择同步间隔。
- 迁移：`038_light_app_schedules.{sql,pg.sql}` + `migrate.py` 幂等 helper；旧 `schedule_seconds > 0` 的 source 自动生成 `sch_src_<source_id>`。

## 3. 必修（合并阻塞）

### 3.1 snapshot schedule 权限与审计

`snapshot_publication` 实际是“定时发布”，但创建/修改/删除只要求 `edit_structure`：

- `src/octop/api/routers/apps.py:3183`（POST）、`:3207`（PATCH）、`:3229`（DELETE）使用 `_write_access(..., "edit_structure")`；
- 对照发布/撤销使用 `_manage_write_access`（`apps.py:2503`）；
- `refresh_publication_snapshot` 每次读取**当前** HTML 与公开表结构并覆盖快照（`src/octop/infra/apps/service.py:5308-5328`）。

协作者可被授予 `edit_structure`（`dashboard/src/pages/Apps/detail/AppShareModal.tsx:98`），而 UI 的发布入口仅 owner/admin 可见（`dashboard/src/pages/Apps/Detail.tsx:125-127`）。临时集成用例已验证：`edit_structure` 协作者 `POST /schedules` 返回 200。该路径也没有 `_audit`。

**建议**：`snapshot_publication` 的写操作改用 `_manage_write_access`（或按 action 区分），补 `light_app.schedule.*` 审计，并把 UI 文案改成“自动发布”而非“自动刷新”。修复后补协作者 403 用例。

**状态（2026-11-15）**：已修复。POST 按 `body.action` 区分权限；PATCH/DELETE 通过新增的 `AppService.get_schedule()` 先解析 action；`snapshot_publication` 统一走 `_manage_write_access`，并新增 `light_app.schedule.save/update/delete` 审计。回归用例 `test_snapshot_schedule_requires_owner` 覆盖协作者 403、sync schedule 仍可由 `edit_structure` 管理、owner 写入审计。UI 文案未改（不在本次范围）。

### 3.2 同一 tick 复用起始 `now`，后续 claim 的租约已过期

`tick()` 只在开头取一次时间（`scheduler.py:113`），之后每个 item 都用同一个 `now` claim 并执行（`scheduler.py:120-128`）。tick 串行 `await`：第一个 `sync_source` 最长 15 分钟，第二个及之后的 `lease_until = now + 300` 在写入时已经过期。

临时用例验证（时钟从 1000 推进到 5000）：第二个任务 claim 得到 `lease_until=1300`，即创建时已过期 3700 秒。心跳要等 60 秒才第一次续租，这段时间另一实例可直接 claim 同一行。

**建议**：每次 claim 前重新取 `int(self._clock())` 并作为 `run_at`；`_run` 开始时立即续租一次。补“长任务后第二个 claim 未过期”用例。

### 3.3 心跳一次异常就永久停止续租

`heartbeat` 的 `except Exception` 分支记录 warning 后直接 `return`（`scheduler.py:148-154`）；`renewed is None` 也只记录日志后返回（`:155-160`）。一次短暂的 `database is locked` 就让 15 分钟任务剩余时间完全不再续租；租约丢失后动作也不会取消，仍继续产生副作用。

**建议**：异常时继续循环重试；确认租约丢失时取消正在执行的动作，并记录指标/日志。与 3.2 同一 PR。

### 3.4 snapshot schedule 缺少唯一约束

迁移只对 `schedule_id` 建唯一约束（`infra/db/migrations/038_light_app_schedules.sql:5-26`），`save_schedule` 是“先查再建”（`service.py:5197-5225`）。两个实例/两个标签页并发时会各建一条 snapshot schedule，之后 PATCH 只更新 `find` 到的第一条，另一条继续独立刷新。

**建议**：趁 v38 未发布，折进 038 加部分唯一索引：

```sql
CREATE UNIQUE INDEX ... ON light_app_schedules(app_id)
WHERE action = 'snapshot_publication';
```

并处理插入冲突（回读已有行）。同步 `migrate.py` helper 与 PG 版本。

## 4. 建议本轮（不阻塞合并，但不修需在 PR 描述写明限制）

1. **sync_source 超时状态不落库**：`run_source` 只在正常/`Exception` 路径 `_record_run`；超时/取消是 `CancelledError`，source 行保持旧状态（`service.py:5330-5391`；`scheduler.py:264-302` fallback 只写 schedule）。临时用例结果：`SOURCE: None None None 0`，`SCHEDULE: error 1000 1065`。而 UI 不展示 sync schedule 的错误，超时是静默失败。建议捕获取消后记录 error 再抛出，或由 `_finish_from_source` 同步写 source。
2. **sync 幂等键按“校验后下标”生成**：`_batch_prefix` 哈希原始 `records`（`service.py:769-799`），但 `client_request_id` 使用 `imported_rows` 下标（`:3600-3613`、`:3664-3690`、`:3716-3736`）。schema 变化使原本失败的行通过时，同一前缀下会跳过错误行并重复写入后续行。建议使用原始记录位置或单行内容哈希。
3. **过期公开链接仍持续刷新**：`save_schedule` 与 `refresh_publication_snapshot` 只检查 revoked（`service.py:5191-5196,5312-5316`），公开访问却会拒绝过期（`:4310-4313`）。建议把过期视同无有效链接，并禁用/删除该 snapshot schedule。
4. **`_finish_*` 在 try/finally 外**：一次 DB 异常会中断整批并遗留租约（`scheduler.py:187-212`）。建议每个 item 外层捕获，并记录 `finish` 返回 False。
5. **`record_run` 无租约条件**：`app_schedules.py:340-366` 可被失去租约的旧进程调用，覆盖合法持有者的 `next_run_at`/`failure_count`。建议调度路径只走带租约的 `finish`，或给 `record_run` 增加租约条件。
6. **删除顺序与迁移孤儿过滤**：`delete_schedule` 先删行、后把 source 置 0（`service.py:5265-5269`），崩溃窗口内重启会复活；`_ensure_light_app_schedules_schema` 每次启动都会执行回填（`migrate.py:3143`）。建议调换顺序，并在迁移 INSERT 中 JOIN `light_apps`/`users` 过滤孤儿；NULL `next_run_at` 可加确定性偏移避免惊群。
7. **文档一致性**：路线图决策 6 写“cron + 服务器时区”，实现是 interval-only（`Daily` 是完成后 24h + 抖动）；实施计划中的详情页“自动化”页签未落地；`miniapp-overview.md` 的定向用例数量已过时。PR 描述应明确这些限制。

## 5. 可延后（记录为已知限制）

- **tick 全局串行**：一个 15 分钟动作会阻塞所有其他应用，`list_due` 上限 100，backlog 指标也被截断；单进程、应用数少的当前定位可接受。建议后续做有界并发（不同应用并发、同一 schedule 不并发）并文档化单车道语义。
- **`TimeoutError` 误判**：`scheduler.py:179-181` 会把动作内部抛出的 builtin `TimeoutError`（如 socket 超时）当成调度硬超时，错误文案写成“after 900s”并计入超时指标。只影响诊断，可顺手用 `asyncio.timeout().expired()` 区分。
- **snapshot 写放大**：每次刷新都覆盖 HTML/表结构，即使内容未变（`service.py:5308-5328`）。规模大时可先比较 `snapshot_digest` 再跳过写。
- **`last_error` i18n**：调度错误存英文原文并直接展示（`AppPublishModal.tsx:231-236`），不符合 AGENTS §7；至少先截断长度，再考虑错误码化。
- **死代码与命名**：`LightAppRepo.list_due_sources` / `claim_source` 已无生产调用（`repos/apps.py:1191-1222`）；038 注释仍提“old source scheduler”；`AppSourceScheduler` 现在管理所有应用级任务。建议后续清理，不与本次修复混在一起。
- **运行历史、连续失败通知、自动停用**：文档已列为待做，当前只保留最后一次状态。

## 6. 范围外问题（另开单）

以下不属于调度，但会影响分支整体发布判断：

- **`GET /api/apps/{id}/backup` 的 user 行越权（已核实）**：路由只要求 `edit_structure`（`apps.py:2234`），`backup_archive` 对每张表用 `include_all_rows=True`（`service.py:3952` 及 `backup_archive`），`row_scope=user` 的表会把所有用户的行写进 zip。若 `row_scope` 随本分支发布，这是该功能的发布阻塞项。最小修法：backup 要求 owner/admin；彻底修法：按 viewer 过滤。
- **restore 不保留 `row_scope` / `public_visible` / PII / created_by**（`service.py:4032+`）：恢复后 user 表会变成协作者共享表，PII 列可能被清空。若 R2.0 声称“备份可恢复”，应作为数据完整性阻塞项；否则随 Bundle v2 做。
- **`app.inbox` 跨应用串数据（已确认）**：`light_app_events` 的幂等键是全局 `event_id`
  （`service.py:4722`、`db/repos/app_events.py:88`），同一事件 fan-out 到两个 app 时第二个 app 会复用
  第一个 app 的行。修复方向见 [event-model.md](./event-model.md) §5。
- 复核提出、尚未逐条复核：`_share_link_warning` 遇 `row_scope=user` 公开表会报错（`tools.py:315`）、
  服务图片先整包缓冲再检查 5 MB（`service.py:759`）且无总量配额、access request pending 不比较
  `needed`（`service.py:1319`）。建议分别建单。

## 7. 建议修复批次

**最小合并集（仅修三项时）**

1. snapshot schedule 权限 + 审计；
2. tick 新鲜时间戳 + 心跳持续重试/租约丢失取消；
3. snapshot 唯一索引（趁 v38）。

对应回归测试：协作者建 snapshot schedule 应 403、长 tick 后第二个 claim 未过期、心跳瞬时异常后仍续租、并发建 snapshot schedule 只留一条。

**同一发布周期第二批**：sync 超时状态、幂等键下标、过期链接、每项收尾隔离 + `record_run` 租约、删除顺序/迁移孤儿过滤、文档/UI 文案。

**明确延后**：串行调度、超时归类、快照写放大、i18n、死代码、运行历史/通知。

**另开单**：备份越权与 restore 往返、inbox 跨应用、share-link warning、图片配额、access request。
