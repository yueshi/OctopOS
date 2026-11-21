---
name: pm-report-archive
description: 归档「项目报告台账」的历史报告期：把主表导出拆成归档 CSV 和保留 CSV，必要时在用户确认后用 replace 导入保留部分。适用于按年清理、表接近 2 万行或用户说归档/清理历史。
---

# 归档项目报告台账

目标：把旧报告期从活动表里移走，同时留下可查的 CSV 证据。**原始 Excel 和导出文件都不删除。**

## 前提

- 只能由 owner/admin 操作。
- 先让用户在应用详情页下载 **主表导出 CSV**（`GET /api/apps/{id}/export?table=main` 的界面入口），放到
  `reports/archive/`。Agent 没有 dashboard JWT，不能自己下载；不要假装已经下载。
- 建议同时下载一次应用备份（应用详情 → 备份），作为回滚点。
- 导出必须由 owner/admin 下载**完整值**；如果导出里的 PII 是脱敏值，不要用 replace 覆盖主表，
  只做归档副本。

## 步骤

1. **确认范围**：问用户保留从哪个报告期开始，例如“保留 2026 年，归档 2025 年及以前”。
   - 周报用 `2026-W01`，日报用 `2026-01-01`，月报用 `2026-01`；不要混用格式。
2. **确认导出文件**：检查 `reports/archive/` 下的 CSV，告诉用户将使用哪个文件。
3. **拆分**：

   ```text
   python skills/pm-report-archive/scripts/archive.py \
     --input reports/archive/<导出文件>.csv \
     --keep-from 2026-W01 \
     --out reports/normalized/
   ```

   输出：

   - `reports/normalized/tasks_retained.csv`：保留行，可直接用于 replace
   - `reports/normalized/tasks_archived.csv`：归档行
   - `reports/normalized/archive_summary.json`：各报告期计数
4. **汇报**：总行数、保留行数、归档行数、涉及的报告期。让用户确认：
   - 只归档（不修改应用）→ 到此结束；
   - 同时清理活动表 → 进入下一步。
5. **清理活动表（可选，必须二次确认）**：

   ```text
   app_import_rows(
     app_id=<app_id>,
     paths=["reports/normalized/tasks_retained.csv"],
     table="main",
     mode="replace",
     dry_run=true
   )
   ```

   把 dry-run 的 `would_import` 与 `archive_summary.json` 的 `retained` 对比，确认一致后再执行
   `dry_run=false`。replace 会整表覆盖主表；执行前必须确认：
   - 用户已经下载备份；
   - 导出文件是当前的、完整的；
   - 保留行数大于 0。

6. **导入记录**：`period_reports` 很小，默认保留不动；如果用户明确要清理，单独导出后同样处理，
   不要顺手删除。

## 失败处理

| 现象 | 处理 |
| --- | --- |
| 导出没有 `period` 列 | 让用户重新导出主表；不要猜列 |
| 报告期格式混用 | 统一成周/日/月，再重跑 |
| 空报告期 | 先修正数据或从导出中排除，脚本会报行号 |
| `retained` 为 0 | 停止；不要把主表清空 |
| replace 的 dry-run 行数不一致 | 停止；导出可能不是最新 |
| 用户没有备份 | 先让用户备份，再继续 |

## 不要做

- 不要删除 `reports/archive/` 下的导出文件或原始 Excel。
- 不要在未确认时执行 `mode="replace"`。
- 不要用 `app_delete_row` 逐行删除来“归档”，慢且容易中断。
- 不要在没有备份的情况下清理主表。
- 不要修改 schema；归档不改表结构。
