# 导入配置说明

`profile.default.json` 是随技能交付的默认解析配置。客户格式不同时，复制一份到工作区
`reports/import-profile.json` 再修改，不要改技能目录里的默认文件。

可以先跑一遍样例验证环境：

```text
python skills/pm-report-ingest/scripts/ingest.py \
  --input skills/pm-report-ingest/references/samples/A项目-2026-W46.csv \
  --out reports/normalized/
```

样例文件是 `references/samples/A项目-2026-W46.csv`，支持 CSV、XLSX、XLSM、XLS。

## 字段

| 字段 | 说明 |
| --- | --- |
| `file_pattern` | 文件名正则，必须包含 `project` / `period` 命名组；没有命名组时靠表内列 |
| `sheet` | 工作表名；`null` 表示活动工作表 |
| `header_row` | 表头行（0-based）；`null` 表示在前 15 行自动探测 |
| `columns` | 源列名 → 目标任务字段；未映射的列忽略 |
| `status_map` | 源状态词 → `not_started` / `doing` / `blocked` / `done` |
| `priority_map` | 源优先级词 → `low` / `medium` / `high` |
| `task_id_fallback` | 没有任务编号时，用哪些字段拼稳定 ID；默认 `project + task_name` |
| `date_formats` | 日期解析格式，按顺序尝试；也接受 ISO 日期/时间 |
| `skip_row_patterns` | 跳过的行正则；合计/小计行通常也能自动识别 |
| `pii_columns` | 默认跳过的敏感列名；需要导入时脚本加 `--include-pii` |
| `health_rules.at_risk_days` | 截止日期距报告期末多少天内算“有风险” |
| `health_rules.delayed_statuses` | 哪些状态直接算“已延期”，默认 `blocked` |
| `default_period_type` | 周期无法从格式推断时的默认类型：`daily` / `weekly` / `monthly` |

## 报告期格式

脚本识别：

- `2026-W46`（ISO 周，周日记为期末）
- `2026-11-19`（日报）
- `2026-11`（月报）

报告期字段用零填充字符串，便于排序；不要写“第46周”这类无法比较的文本。

## 任务编号

优先使用源表的任务编号列。没有时用 `project + task_name` 拼接；任务改名会被当成新任务，
所以长期使用建议在 Excel 里补一个稳定编号。

## 常见调整

- 表头不在第一行：设 `header_row`（0-based）。
- 一个文件包含多个项目：在 `columns` 里映射「项目」列，文件名可以只带周期。
- 状态词不同：补 `status_map`，不要改脚本。
- 日期是 Excel 序列号：另存为日期格式，或在 `date_formats` 里补对应格式。
- 进度是 0–1 小数：脚本会把 `0 < x < 1` 自动换算成百分比；`0.8` → 80，`1` 仍按 1% 处理。
- 公式单元格没有缓存值：在 Excel 里复制为“值”后另存，或让脚本读到计算后的结果。
