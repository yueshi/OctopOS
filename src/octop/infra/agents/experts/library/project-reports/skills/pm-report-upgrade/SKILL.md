---
name: pm-report-upgrade
description: 把已有的「项目报告台账」升级到当前模板：补齐 projects / period_reports 表、追加缺失列、替换为最新界面，并保留全部数据。用户说升级、更新台账、界面不对或缺少项目档案时读取。
---

# 升级项目报告台账

只做增量：补表、补列、换界面。**不删除、不重建、不清空任何数据。**

## 步骤

1. **找到应用**：`app_list`。如果有多个同名应用，先问用户是哪一个；不要猜。

2. **读取当前状态**：
   - `app_list` 返回每项的表清单；记下 `period_reports`、`projects` 是否存在。
   - `app_read_data(app_id=<app_id>, table="main", limit=1)` 返回主表 `columns`；记下已有列名。
   - `app_read_data(app_id=<app_id>, table="period_reports", limit=1)` 返回导入记录列（如果表存在）。

3. **补附加表**：读取 `skills/pm-report-ingest/references/tables.json`。
   - `period_reports` 不存在 → `app_add_table(key="period_reports", label="导入记录", public=False, columns=<columns>)`。
   - `projects` 不存在 → `app_add_table(key="projects", label="项目档案", public=False, columns=<columns>)`。
   - 已存在 → 跳过，不要重复创建。

4. **补主表列**：读取 `app_templates/project-reports/schema.json`，对比主表 `columns`。
   - 只追加缺失列，调用：

     ```text
     app_update_schema(
       app_id=<app_id>,
       table="main",
       columns=[<缺失列定义>]
     )
     ```

   - 追加时把 `required` 设为 `false`；已有数据时不能加必填列。
   - 如果缺的是 `project` / `period` / `task_id` / `task_name` 这类核心列，停止并报告“这不是当前模板的台账”，不要强行补。

5. **补导入记录列**：对比 `period_reports` 的列与 `tables.json`，缺失列同样用
   `app_update_schema(table="period_reports", columns=[...])` 追加；不要改已有列类型。

6. **替换界面**：先告诉用户“将用最新模板覆盖界面，数据不受影响”，确认后：
   - 读取 `app_templates/project-reports/app.html`；
   - `app_write_html(app_id=<app_id>, html=<内容>)`。

7. **重置能力**：`app_set_capabilities(app_id=<app_id>, capabilities=["data.read"])`。

8. **汇报**：
   - 新建了哪些表、追加了哪些列、界面是否已更新；
   - 没有改动任何行；
   - `app_open` 打开应用。

## 失败处理

| 现象 | 处理 |
| --- | --- |
| `table already exists` | 跳过该表，继续下一项 |
| `app_update_schema` 拒绝加列 | 只加可选列；不要改已有列或删除列 |
| `app_write_html` 被拒 | 报告校验错误，不要删除应用；界面保持旧版 |
| 主表缺少核心列 | 停止；说明这不是当前模板的台账，建议新建应用 |
| 用户不确定是否升级 | 先只补表/列，暂不覆盖 HTML |

## 不要做

- 不要 `app_delete` / `app_delete_row` 清理旧数据。
- 不要用 `app_import_rows(mode="replace")` 做升级。
- 不要改 `period_reports` / `projects` 的已有列类型。
- 不要在未确认时覆盖用户自定义过的界面；用户明确要求保留界面时，只补表/列。
