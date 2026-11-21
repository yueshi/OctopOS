---
name: pm-report-init
description: 初始化「项目报告台账」：创建 MiniApp、补齐导入记录表、设置只读页面能力，并准备 reports/ 导入目录。用户第一次使用、应用丢失或表结构不完整时读取。
---

# 初始化项目报告台账

目标是让当前 Agent 拥有一个可用的「项目报告台账」应用。只做一次；重复执行前先检查。

## 步骤

1. **查重**：调用 `app_list`。如果已有名称为「项目报告台账」的应用：
   - 调用 `app_open` 打开它；
   - 用 `app_read_data` 快速确认 `period_reports` 表是否存在；
   - 告诉用户“台账已存在”，不要重复创建。

2. **建应用**：

   ```text
   app_create_from_template(
     template="project-reports",
     source="agent",
     name="项目报告台账"
   )
   ```

   模板只创建主表 `main`（任务明细）。

3. **补附加表**：读取 `skills/pm-report-ingest/references/tables.json`，依次创建
   `period_reports`（导入记录）和 `projects`（项目档案）：

   ```text
   app_add_table(
     app_id=<上一步的 app_id>,
     key="period_reports",
     label="导入记录",
     public=False,
     columns=<period_reports.columns>
   )

   app_add_table(
     app_id=<上一步的 app_id>,
     key="projects",
     label="项目档案",
     public=False,
     columns=<projects.columns>
   )
   ```

   两张表都是私有的。如果工具返回“table already exists”，跳过，不要重建。

4. **设置页面能力**：页面只读，不需要写数据能力。

   ```text
   app_set_capabilities(
     app_id=<app_id>,
     capabilities=["data.read"]
   )
   ```

5. **准备目录**：确认工作区有 `reports/`。没有就创建一个 `reports/README.md`，写明命名建议：
   `项目名-2026-W46.xlsx` 或 `项目名-2026-11-19.xlsx`；也支持一个文件包含多个项目（必须有项目列）。

6. **打开应用并交接**：调用 `app_open`，然后告诉用户：
   - 台账已创建；
   - 把本周 Excel 放进 `reports/`，或直接在对话里上传；
   - 下一步说「导入本期报告」。

## 失败处理

| 现象 | 处理 |
| --- | --- |
| `app_create_from_template` 找不到模板 | 确认当前 Agent 工作区有 `app_templates/project-reports/`；没有就报告模板缺失，不要改用 `app_create` 猜字段 |
| `app_add_table` 超过表数上限 | 应用里已有 12 张表，停止并让用户清理 |
| `app_set_capabilities` 被拒 | 只传低风险能力；不要尝试 `ai.generate` / `external.fetch` / `events.emit` |
| 已有应用但缺 `period_reports` / `projects` | 只补表，不重建应用；不要清空已有数据 |

## 不要做

- 不要为每个项目建一个应用。
- 不要用 `app_write_html` 重写模板界面；界面由模板提供。
- 不要在初始化时导入任何数据。
- 不要把 `period_reports` 设为公开。
