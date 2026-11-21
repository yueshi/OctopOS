# 项目台账操作手册

本文件是技术规范。SOUL.md 讲原则，这里讲怎么做。

## 1. 工作区约定

```text
reports/                         原始 Excel，只读留档
  import-profile.json            可选：客户自定义解析配置
  normalized/                    脚本输出：tasks.csv / period_reports.csv / manifest.json

skills/pm-report-init/           初始化技能
skills/pm-report-ingest/         导入技能
  scripts/ingest.py              确定性解析与归一化
  references/profile.default.json 默认解析配置
skills/pm-report-review/         复盘技能（只读）
app_templates/project-reports/   应用模板
```

- 用户上传的聊天文件通常落在 `inbound/`；导入时同时扫描 `reports/` 和用户指定的路径。
- `reports/normalized/` 是临时产物，可以覆盖；不要把它当原始证据。
- 不移动、不改名、不删除 `reports/` 下的原始文件。

## 2. 应用模型

- 应用 `main` 表 = 任务明细；主表 key 固定为 `"main"`，不要写成 `"tasks"`。
- `period_reports` 表 = 导入记录（私有，不出现在公开链接）。
- `projects` 表 = 项目档案（私有）：`project_name` / `owner` / `status` / 起止日期。
  可选；缺失或没有对应行时，页面直接用 `project` 值展示。
- 粒度：`project × period × task_id` 一行；`tasks` 的 `upsert_key` 是这三个字段。
- `period_reports` 的 `upsert_key` 是 `batch_id`；`batch_id` 由文件 hash + 报告期决定。
- 溯源：任务行带 `batch_id`；来源文件路径只存在 `period_reports`，不要写进任务表。

## 3. 常用工具

| 工具 | 用途 | 关键约束 |
| --- | --- | --- |
| `app_list` | 查当前 agent 的应用 | 先查再建，避免重复 |
| `app_create_from_template` | 从 `project-reports` 模板建应用 | 只建主表；不会自动加 `period_reports` |
| `app_add_table` | 加 `period_reports` 表 | key 唯一；每应用最多 12 张表；默认不公开 |
| `app_set_capabilities` | 设置页面能力 | 只设低风险能力；本方案页面只读，只给 `data.read`，不要开写能力 |
| `app_import_rows` | 从工作区 CSV 批量导入 | 先 `dry_run=true`；`upsert_key` 必传 |
| `app_read_data` | 读一页数据 | `limit` 最大 500；查 `period_reports` 已知 hash |
| `app_open` | 打开应用卡片 | 初始化完成后调用一次 |
| `app_share_link` | 创建只读公开链接 | 仅 owner/admin；先确认用户要分享 |
| `app_set_table_visibility` | 表级公开开关 | 主表默认公开；`period_reports` 保持私有 |
| `execute_shell_command` | 运行技能脚本 | 在工作区根目录运行；脚本不访问网络/数据库 |

所有工具返回 JSON 字符串；失败时读 `error` 再改参数，不盲目重试。

## 4. 初始化流程

1. `app_list` 查是否已有「项目报告台账」；有就 `app_open` 并汇报状态，不重复创建。
2. `app_create_from_template(template="project-reports", source="agent", name="项目报告台账")`。
3. 读取 `skills/pm-report-ingest/references/tables.json`，用其中的定义调用：
   `app_add_table(key="period_reports", label="导入记录", public=False, columns=[...])`；
   `app_add_table(key="projects", label="项目档案", public=False, columns=[...])`。
   两张表都私有；已存在就跳过。
4. `app_set_capabilities(["data.read"])`。页面只读；数据通过导入更新。
5. 确认/创建 `reports/` 目录；告诉用户文件命名建议（项目-周期.xlsx）。
6. 告诉用户下一步：把本周 Excel 放进 `reports/`，或上传到对话，然后说「导入本期报告」。

## 5. 导入流程

1. 读 `skills/pm-report-ingest/SKILL.md`。
2. 先 `app_read_data(table="period_reports", limit=500)` 取已知 `source_hash`（一年约 52 条，一页够）。
3. 扫描 `reports/`、`inbound/` 或用户给的路径；跳过 `reports/normalized/` 和已导入 hash。
4. 运行：

   ```text
   python skills/pm-report-ingest/scripts/ingest.py \
     --input reports/ \
     --profile reports/import-profile.json \
     --out reports/normalized/
   ```

   没有自定义 profile 时省略 `--profile`，脚本会用技能内的默认配置。
5. 读 `reports/normalized/manifest.json`，向用户汇报：文件数、任务数、失败行、涉及报告期。
6. 先 dry-run 导入任务：

   ```text
   app_import_rows(
     app_id=...,
     paths=["reports/normalized/tasks.csv"],
     table="main",
     dry_run=true,
     upsert_key=["project", "period", "task_id"]
   )
   ```

7. 用户确认后正式导入 tasks；再导入 `period_reports`，`upsert_key=["batch_id"]`。
8. 汇报新增/更新/失败/跳过；必要时 `app_open`。

## 6. 导入规则

- **失败行不阻断整份文件。** 脚本输出 `failed` 列表；先展示前 5 条，问用户是否修正后重导。
- **重复文件跳过。** 同一 `source_hash` 已存在时，不要重复导入；如需强行重导，先征得用户同意。
- **同一报告期重导 = 更新。** `upsert_key` 会更新该期任务，不产生重复；不要删除旧期。
- **PII 默认跳过。** 脚本按 `profile.pii_columns` 跳过敏感列；平台导入也会检测。用户明确要求时才加 `--include-pii`，并再确认一次。
- **不要自动删除旧行。** 新报告里消失的任务可能只是本期未上报；不要用 `app_delete_row` 批量清理。
- **不要改 HTML。** 界面由模板提供；确有需求时先说明，再用 `app_write_html` 整页覆盖。

## 7. 失败处理

| 现象 | 处理 |
| --- | --- |
| `cannot detect the header row` | 让用户确认表头行，或在 `reports/import-profile.json` 设 `header_row`（0-based） |
| `missing project/period` | 检查文件名是否含项目/周期，或在 profile 加列映射 / 用 `--period` |
| `unknown status` | 让用户补 `status_map`；不要猜 |
| `invalid date` / `invalid progress` | 展示行号和原值，请用户修正 Excel 或补 `date_formats` |
| PII 被跳过 | 展示列名；只有用户明确同意才重导 |
| 表超过 20,000 行 | 提醒按年归档；不要静默截断 |
| `period_reports` 表不存在 | 先跑初始化技能补表 |

## 8. 公开链接

- 只有 `main` 表默认公开；`period_reports` 保持私有。
- 公开页面不显示来源文件、导入时间、失败摘要。
- 分享前说清受众；只读链接是 bearer token 语义。
- 需要按行子集公开时，按平台规则建独立公开投影表，不要靠页面过滤。

## 9. 升级与归档

- **升级**：读 `skills/pm-report-upgrade/SKILL.md`。只补 `projects` / `period_reports` 表、追加缺失列、替换 HTML；
  不删除、不重建、不清空数据。覆盖界面前先确认。
- **归档**：读 `skills/pm-report-archive/SKILL.md`。先让用户从应用详情页下载主表 CSV 和应用备份，
  再运行 `archive.py` 拆成 retained / archived；只有用户二次确认后才用
  `app_import_rows(mode="replace")` 清理活动表。原始 Excel 和导出文件不删。
- 项目档案：`projects` 表可按需 `app_import_rows` 导入，`upsert_key=["project"]`；
  不要为了填档案而覆盖任务表。

## 10. 边界

- 不直接读写数据库，不调用外部 HTTP API。
- 不在应用页面里引远程脚本/样式/图片/字体；页面必须自包含。
- 不把一个项目做成一个应用；项目是 `project` 列。
- 不做审批流、不做多页面 HTML、不做跨应用共享表。
- 不编造数字；缺数据就写“未提供”。
