---
name: pm-report-ingest
description: 把同格式的项目周报/日报 Excel 批量导入「项目报告台账」。扫描工作区、按 profile 归一化、先 dry-run 再入库，并记录来源文件与失败行。
---

# 导入项目报告

目标：把 `reports/`（或用户指定目录）里新增的 Excel 变成任务快照行，不重复、不丢行、可追溯。

## 输入

- 原始文件：`reports/*.xlsx`、`reports/*.xls`、`reports/*.csv`，或对话上传到 `inbound/` 的文件。
- 解析配置：默认用技能内的 `references/profile.default.json`；客户自定义时用
  `reports/import-profile.json`。字段说明见 `references/README.md`。
- 目标应用：当前 Agent 的「项目报告台账」；先用 `app_list` 找到 `app_id`。

## 步骤

### 1. 查已知文件

```text
app_read_data(app_id=<app_id>, table="period_reports", limit=500)
```

把返回行里的 `source_hash` 记下来；同一 hash 的文件已经导入过，默认跳过。

### 2. 扫描文件

检查 `reports/`、`inbound/` 或用户指定的路径。跳过：

- `reports/normalized/`（脚本输出目录）；
- 隐藏文件；
- 已导入 hash 的文件。

如果没有新文件，告诉用户“没有发现新的报告”，不要重复导入。

### 3. 运行归一化脚本

在工作区根目录执行：

```text
python skills/pm-report-ingest/scripts/ingest.py \
  --input reports/ \
  --profile reports/import-profile.json \
  --out reports/normalized/
```

没有自定义 profile 时省略 `--profile`：

```text
python skills/pm-report-ingest/scripts/ingest.py \
  --input reports/ \
  --out reports/normalized/
```

脚本输出：

- `reports/normalized/tasks.csv`
- `reports/normalized/period_reports.csv`
- `reports/normalized/manifest.json`

### 4. 读 manifest 并汇报

读取 `reports/normalized/manifest.json`，向用户汇报：

- 读取文件数、解析任务数、失败行数；
- 涉及的报告期；
- 前 5 条失败行（行号 + 原因）；
- 被跳过的 PII 列。

如果 `tasks` 为 0 或有失败行，先停下来让用户确认，不要直接入库。

### 5. 预检导入任务

```text
app_import_rows(
  app_id=<app_id>,
  paths=["reports/normalized/tasks.csv"],
  table="main",
  mode="append",
  dry_run=true,
  upsert_key=["project", "period", "task_id"]
)
```

把 dry-run 的 `would_import` / `skipped` / `errors` 告诉用户。

### 6. 正式导入

用户确认后，用同样的参数但 `dry_run=false` 导入 tasks；然后导入导入记录：

```text
app_import_rows(
  app_id=<app_id>,
  paths=["reports/normalized/period_reports.csv"],
  table="period_reports",
  mode="append",
  upsert_key=["batch_id"]
)
```

顺序必须是先 tasks、后 period_reports；这样失败时任务行仍在，导入记录可重试。

### 7. 收尾

- 汇报：新增/更新/失败/跳过；涉及报告期；
- 如果有失败行，列出修正建议；
- 需要时 `app_open` 打开台账；
- 不要删除 `reports/normalized/` 之外的任何文件。

## 解析配置

默认 profile 支持：

- 文件名 `项目-2026-W46.xlsx` / `项目_2026-11-19.xlsx`；
- 表中包含「项目」「周期」列时优先用行值（适合一个文件多个项目）；
- 状态同义词（未开始/待办/进行中/处理中/阻塞/已完成）和优先级映射；
- 日期格式和进度百分比；
- 合计/小计行自动跳过；
- 身份证/银行卡/手机号/薪资列默认跳过。

需要改字段名、表头行、状态词时，复制 `references/profile.default.json` 到
`reports/import-profile.json` 再修改；不要改技能目录里的默认文件。

## 失败处理

| 现象 | 处理 |
| --- | --- |
| 脚本退出码 2 | 有失败行；先看 manifest，修正后再导入 |
| 脚本退出码 3 | 没有可导入文件或 profile 无效；检查路径和 JSON |
| `unknown status` | 在 profile 的 `status_map` 里补映射 |
| `missing project/period` | 检查文件名，或在 profile 里映射项目/周期列 |
| `cannot detect the header row` | 在 profile 设置 `header_row`（0-based） |
| 平台导入 `APP_ROW_INVALID` | 看 `errors` / `failed_rows`；不要绕过 schema 改列 |

## 不要做

- 不要跳过 dry-run 直接入库。
- 不要为同一份文件重复导入；除非用户明确要求覆盖。
- 不要用 `app_delete_row` 批量清理旧期；历史报告期是快照。
- 不要把来源文件路径写进任务表；它只存在 `period_reports.source_file`。
- 不要因为 1 行失败就丢弃整份文件。
