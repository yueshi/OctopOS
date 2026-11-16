# 事件模型与投递契约

> 状态：2026-11-15 整理。本文是 Octop 各类 Event 的**权威术语与保证说明**；
> 具体实现见各平面代码与设计文档。相关文档：
> [external-events-design.md](./external-events-design.md)、
> [miniapp-overview.md](./miniapp-overview.md)、
> [team-drive-v2.md](./team-drive-v2.md)、
> [miniapp-event-api-design.md](./miniapp-event-api-design.md)。

---

## 0. 结论

Octop **没有统一 EventBus，也不需要**。事件按用途分成五类，各自选择存储与投递方式；
共同底线是：

- 事件是**不可变事实**，不是命令。需要执行动作时，由 target adapter 把事实转成一次受控执行。
- 持久化事件都是 **at-least-once**；消费方必须幂等，或明确声明“不自动重试”。
- payload 永远不能决定 target / mode / table / user；target 由 owner 预先配置。
- WS/SSE 帧只是加速，不是真相；页面必须能从持久化接口恢复。
- 不为“统一”引入外部队列；DB inbox/outbox + 条件 UPDATE 租约是当前规模下的正确基础。

---

## 1. 五类事件

| 类型 | 存储 / 传输 | 用途 | 顺序 | 幂等键 | 投递保证 | 保留 | 真相 |
|---|---|---|---|---|---|---|---|
| **入站事实 + outbox** | `external_sources` → `external_events` → `external_deliveries` | 外部 webhook / connector poll 进入系统，fan-out 到 expert/team/app | 无全局顺序；按 target 独立重试 | `(source_id, external_id)`；缺失时 payload hash + 分钟桶 | at-least-once；lease/retry/dead-letter | 事件与投递 30 天 | `external_events` |
| **聚合账本** | `team_events` | 单团队控制面事实：成员回复、看板变更、审批、外部事件、心跳 | 每团队 `seq` 单调 | `team_id + dedupe_key` | 先落库再路由；`consumed_at` 水位 | 设计目标 30 天，**当前未实现清理** | `team_events` |
| **消费端收件箱** | `light_app_events` | MiniApp 页面按游标读取、ack；外部事件的投影 | 每 app 按自增 `id` | `(app_id, external_event_id)`（目标；当前实现见 §5） | 投递写库后页面可回放 | 每 app 最多 500 条 / 30 天 | `external_events` + 投影 |
| **轨迹日志** | `trajectory_events` | 会话执行轨迹、调试与回放 | 每 thread `seq` | 稳定 `event_id`（`thread:seq:kind`） | 随回合写入；可裁剪 | 按用户轮次裁剪 | `trajectory_events` |
| **实时信号** | WS/SSE 帧（`dashboard_push`、`team.*`、`app_event`、`turn.*`） | 通知、加速、唤醒 UI | 不保证 | 无（best-effort） | 无重试、无回放 | 不保留 | 对应持久化平面 |

此外，`team_waits`、`team_decisions`、`hitl_pending`、`audit_log`、`usage_log`
是**控制记录/审计记录**，不是事件流；不要把状态机塞进事件表。

### 1.1 新增事件时怎么选

1. 需要跨重启恢复、要 fan-out、要独立重试 → **入站事实 + outbox**（`external_events`）。
2. 只属于一个聚合（团队/会话），需要严格顺序和回放 → **聚合账本**。
3. 一个消费者按游标消费、需要 ack → **收件箱**。
4. 只是让打开的页面尽快更新 → **WS/SSE 信号**；同时提供持久化查询接口。
5. 表达“现在处于什么状态、在等谁” → **控制表**，不要新增事件类型。

回答不了“属于哪一类”的需求，先不要加事件。

---

## 2. 跨平面链路

```text
webhook / connector poll
  → external_events(event_id)
  → external_deliveries(delivery_id)
      ├─ expert.turn  → usage_log(source=external_event), thread history
      ├─ team.judge   → team_events(type=external.event, payload.event_id)
      └─ app.inbox    → light_app_events(external_event_id)
```

理想情况下，`event_id` 应能贯穿整条链路；当前 expert/team 已带 `event_id`，
app inbox 目前把它编码进 `event_id`（`ext_{external_event_id}`），没有独立列。
新增 `GET /api/external/events/{event_id}` 作为“事件旅程”查询是后续工作
（见 §5）。

---

## 3. 事件信封（新事件的推荐字段）

内部事件不强制统一 schema，但新事件应尽量使用稳定字段：

```json
{
  "event_id": "evt_...",
  "event_type": "order.created",
  "occurred_at": 1790000000,
  "received_at": 1790000001,
  "source_id": "src_...",
  "subject": "app_7F3K2Q/tables/orders",
  "summary": "订单 #123 已创建，金额 ¥...",
  "payload": {}
}
```

规则：

- `event_type` 使用 `<domain>.<aggregate>.<action>`（如 `app.row.created`、`team.task.changed`）。
  不为历史类型做批量迁移，但新类型必须遵守。
- 面向 prompt/UI 的文本放 `summary`；完整数据放 `payload`；大文件不要塞事件（走 import/artifact）。
- 时间用 UTC epoch 秒；展示走服务器时区。
- 新增事件类型时写明：生产者、消费者、幂等键、保留期、是否可重放。

---

## 4. 投递语义

- 外部平面是 **at-least-once**，不是 exactly-once。
- 每个 target 自己负责幂等：
  - `team.judge`：`dedupe_key=external:{event_id}` 写入账本，重试安全。
  - `app.inbox`：按外部事件 id 幂等（目标作用域为 app，见 §5）。
  - `app.import`：按 `client_request_id` / `upsert_key` 幂等。
  - `expert.turn`：**当前没有幂等标记**，崩溃后重试可能重复跑一轮 LLM。
    在修好之前，把 `expert.turn` 视为“可能重复”，不要承诺一次。
- `max_per_minute` 在 ingest 侧命中时目前是静默跳过订阅；发布方仍会收到 202。
  需要把“被限流”作为可观测结果（见 §5）。
- `status` 字段的语义要按平面区分：
  `external_events.status` 表示**入队时的 fan-out 状态**（`no_subscribers` / `dispatched`），
  不表示最终投递成功；最终结果看 `external_deliveries.status`。

---

## 5. 已知缺口（2026-11-15）

以下问题已确认，修复前不要把它们当作已保证的行为：

### P0 — 正确性

1. **`light_app_events` 的幂等键没有按 app 作用域。**
   `AppService.append_event` 用 `ext_{external_event_id}` 做全局唯一 id，并按该 id 回查
   （`src/octop/infra/apps/service.py:4722-4726`；`src/octop/infra/db/repos/app_events.py:88-94`）。
   同一外部事件 fan-out 到两个 app 时，第二个 app 会拿到第一个 app 的事件视图，
   自己的 inbox 为空，delivery 仍记为 `delivered`。
   修复方向：增加 `external_event_id` 列 + `UNIQUE(app_id, external_event_id)`。

2. **PostgreSQL 并发路径不是冲突安全的。**
   `external_events.ingest` 先 SELECT 再 INSERT（`src/octop/infra/db/repos/external_events.py:85-90`），
   PG 并发重复请求会撞 UNIQUE 返回 500；
   `team_events` 的 `seq` 用 `MAX(seq)+1`（`src/octop/infra/db/repos/team_events.py:82-100`），
   并发写会撞 `UNIQUE(team_id, seq)`，而 `TeamCoordinator.record` 会吞掉异常
   （`src/octop/infra/agents/teams/coordinator.py:174-184`），造成事件静默丢失。
   SQLite 的 `BEGIN IMMEDIATE` 掩盖了这两个问题。

3. **`app.import` / `app.refresh` 的执行结果被吞掉。**
   worker 不读取 `ImportResult` 的 `skipped` / `errors` / `warnings` / `blocked_pii`
   （`src/octop/infra/events/worker.py:661-670`），部分行失败或 PII 列被跳过时，
   delivery 仍标记为 `delivered`。
   另外 `import_rows` 默认 `include_pii=False`（`src/octop/infra/apps/service.py:3472`），
   `mode="replace"` 会把 PII 列置空后整表覆盖（`:3555-3557`、`:3657`）。

4. **`expert.turn` 的重试可能重复执行。**
   `CronDeliveryService.deliver` 不按 `cron_id` / `delivery_id` 去重
   （`src/octop/infra/cron/delivery.py:71-90`）。LLM turn 成功但 `finish(delivered)`
   前崩溃时，租约过期会再次执行。

### P1 — 可观测性与规模

5. **事件 id 熵不足。** `new_short_id()` 默认 6 字符（约 30 bit，
   `src/octop/infra/utils/ulid.py:15-17`），被 `external_events`、`external_deliveries`、
   `team_events`、`external_sources`、`external_subscriptions` 使用；高频事件下
   UNIQUE 冲突概率随行数上升。事件平面应改用 12–16 字符或 ULID，并统一前缀
   （`evt_` / `dlv_` / `src_` / `sub_` / `tev_`）。
6. **限流命中静默。** `src/octop/infra/events/service.py:546-550` 直接 `continue`，
   没有 `rate_limited` 记录或指标。
7. **`app_event` WS 帧没有消费者。** worker 会推送（`src/octop/infra/events/worker.py:632-642`），
   但 dashboard 源码中没有处理该帧的分支；当前只有 `app.inbox` 的轮询接口可用。
8. **保留期不齐。** 外部事件 30 天硬编码；`team_events` 没有清理；
   `light_app_events` 只在写入时 prune。
9. **顺序未定义。** 外部平面没有 `ordering_key`，同一 app 的并发投递可能乱序；
   `app.import` 的 `replace` 没有 `snapshot_version` 水位保护。
10. **payload 治理。** 设计文档提到 artifact，但实现把完整 payload 存在
    `external_events.payload_json`（最大 256 KiB）；PII 脱敏与保留策略需要明确。

---

## 6. 扩展规则

- **不要新增第二套投递引擎。** 需要 fan-out/重试时复用 `infra/events`。
- **不要做全平台 EventBus。** 需要进程内通知时用 WS/SSE；需要持久化时用 inbox/outbox。
- **不要让 payload 选 target。** 发布方只能写事件；target/mode 由 owner 的 subscription 决定。
- **不要承诺 exactly-once。** 文档和 UI 都用“至少一次，消费方幂等”的措辞。
- **MiniApp 出站事件（`events.emit`）**：先定 envelope 和 app-scoped 授权，
  再复用 `ExternalEventService.ingest`；app inbox 作为投影，不做独立事件总线。
  详细取舍见 [miniapp-event-api-design.md](./miniapp-event-api-design.md)。
- **多进程**：限流、coalesce、`WebSocketHub` 都是进程内状态；在未迁移到 DB/外部分发前，
  明确声明单进程边界。
