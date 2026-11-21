# MiniApp 创建审批复盘：app_69QYSXVXCP

> 快照：2026-11-19，分支 `feature/miniapp-capabilities`。
>
> 对象：`app_69QYSXVXCP`（我的书斋），Agent `MyBuddy`（`0M0V3X`），线程
> `thr_01M5KM138WB4CWBQCTM7G59VD4`。
>
> 数据来源：`~/.octop/octop.db` 的 `trajectory_events`、`hitl_pending`、
> `light_apps`，以及 Agent 会话 JSONL。

## 1. 创建链路

| 时间（2026-11-19） | 事件 |
|---|---|
| 19:32:19 | 用户：「复制一份同款，功能类似，体验类似」 |
| 19:32:42 | `app_create` 创建 `app_69QYSXVXCP`（我的书斋） |
| 19:32:48 | `app_import_rows` 导入 17 行 → **审批 1** |
| 19:35:15 | 审批通过；导入成功（17 行） |
| 19:40:50 | `app_add_service` → **审批 2**（provider=connector，未带实例） |
| 19:55:09 | 审批通过；工具返回 `connector_instance_id is required`（无效调用） |
| 19:56:13 | 修正为 provider=external + connector_kind=douban → **审批 3** |
| 19:57:07 | 审批通过；服务声明成功 |
| 19:57:20 | `app_set_capabilities`（含 `external.fetch`）→ **审批 4** |
| 19:57:59 | 审批通过；工具返回 `capability requires owner approval: external.fetch`（无效调用） |
| 20:33:15 | 用户再次要求开启 `external.fetch` → 相同能力的**审批 5**（当前仍 pending） |

创建本身（数据导入 + 服务声明 + 页面部署）在 20:00 前完成；审批等待约占
19:32–19:58 的大部分时间，其中审批 2 等待约 14 分钟。

## 2. 审批明细

| # | 工具 | 参数要点 | 结果 | 等待 |
|---|---|---|---|---|
| 1 | `app_import_rows` | `path=inbound/imports/reading_records.csv` | 成功，17 行 | 147s |
| 2 | `app_add_service` | `provider=connector`，无 `connector_instance_id` | **失败**：缺连接器实例 | 858s |
| 3 | `app_add_service` | `provider=external` + `connector_kind=douban` | 成功 | 54s |
| 4 | `app_set_capabilities` | 含 Owner-only `external.fetch` | **失败**：只能 Owner 开启 | 39s |
| 5 | `app_set_capabilities` | 与 #4 完全相同 | 仍 pending | — |

## 3. 根因

1. **顺序调用 → 每步一张卡**：模型逐轮调用 `app_import_rows`、
   `app_add_service`、`app_set_capabilities`，没有把互不依赖的敏感调用放在
   同一轮，因此无法利用审批卡本身的多 action 合并能力。
2. **审批前不校验参数**：HITL 在工具执行前触发，`provider=connector` 缺实例
   必然失败，却仍让用户先批准。
3. **Owner-only 能力重试**：`external.fetch` 不在
   `AGENT_SETTABLE_CAPABILITIES` 内，工具必然拒绝；模型仍调用两次，产生重复审批。
4. **MiniApp 审批不可放通**：此前所有敏感 app 工具都在
   `MANDATORY_HITL_TOOLS` 中，审批卡的「放通这些工具（本对话）」对
   `app_import_rows` / `app_add_service` / `app_set_capabilities` 不生效。

## 4. 本次改进

| 改动 | 位置 |
|---|---|
| 搭建类工具改为可放通：`APP_SETUP_HITL_TOOLS` = `app_import_rows` / `app_add_service` / `app_set_capabilities`；放通后本对话同类调用不再弹卡 | `infra/apps/tool_policy.py`、`infra/agents/security/hitl_session.py` |
| 所有敏感 app 工具仍强制进入 HITL；破坏性/安全类（删应用、删行、改结构、公开表、分享、服务删除、集成写操作）仍不可放通 | `FORCED_HITL_TOOLS`、`MANDATORY_HITL_TOOLS` |
| 审批前预检：Owner-only 能力、`provider=connector` 缺实例、未知能力名、缺 `app_id` 等必然失败的调用跳过审批卡，直接返回参数错误 | `infra/apps/hitl_preflight.py`、`infra/agents/manager.py` |
| Agent 提示词：提醒把互不依赖的敏感调用放到同一轮、不要重复已批准的相同调用、Owner-only 能力引导到应用详情页 | `i18n/{zh,en}.json` 的 `light_apps.system_prompt` |
| `app_add_service` 描述明确 provider/connector 约束；`app_set_capabilities` 明确拒绝后不要重试 | `infra/apps/tools.py` |
| 审批策略提示文案说明：放通后破坏性 MiniApp 操作仍需确认 | `dashboard/src/locales/{zh,en}.json` |

## 5. 预期效果

以同样的创建流程重放：

- `app_import_rows`：一张卡；用户可点「放通这些工具（本对话）」，
  后续导入不再询问。
- `app_add_service`（provider=connector 缺实例）：**不弹卡**，直接返回错误，
  模型改用 `provider=external` 后只弹一次卡。
- `app_set_capabilities`（含 `external.fetch`）：**不弹卡**，直接返回错误，
  模型应引导 Owner 到应用详情页开启。
- 有效的 `app_add_service` + `app_set_capabilities` 若在同一轮发出：
  一张卡包含两个 action，一次批准。

审批 5（`hitl_pending` id 18）属于历史遗留的 pending 记录，本次不自动处理；
可拒绝该卡，或由 Owner 在应用详情页手动开启 `external.fetch`。
