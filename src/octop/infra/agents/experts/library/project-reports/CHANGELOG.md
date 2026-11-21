# 项目报告台账 · 变更记录

## 1.1.0 — 2026-11-19

- 新增 `projects` 项目档案表（私有）：正式名称、负责人、项目状态、起止日期。
- 新增 `pm-report-upgrade`：补表、补可选列、替换界面，不删除数据。
- 新增 `pm-report-archive` + `archive.py`：导出拆分、dry-run、二次确认后 replace。
- 公开链接降级模式：公开 SDK 没有 `aggregate` 时展示最新一期，筛选在前端完成。
- 归档/升级文档与测试。

## 1.0.0 — 2026-11-19

- 初始方案包：`pm-report-init` / `pm-report-ingest` / `pm-report-review`。
- MiniApp 四页签：本期任务 / 项目总览 / 历史报告 / 风险清单。
- 确定性导入：CSV/XLSX/XLSM/XLS、状态/优先级/日期/进度归一化、PII 跳过、
  失败行报告、重复任务折叠、batch_id 幂等。
