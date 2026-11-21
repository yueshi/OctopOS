# 项目报告台账方案落地计划

> 日期：2026-11-19
> 分支：`feature/miniapp-capabilities`（本文为设计稿；实现另开 `feature/project-reports-solution`，从 `develop` 切出）
> 状态：待评审 / M0 未开始
>
> 口径：本文是「项目报告台账」方案的落地计划，回答**交付什么、怎么分层、按什么顺序做、如何验收**。
> 实现现状以代码为准；平台能力边界见 [MiniApp 功能与架构现状](./miniapp-overview.md) 与
> [MiniApp 产品定位与路线图](./miniapp-product-plan.md)。

---

## 0. 结论摘要

做一个**内置专家方案包**：专家负责导入与复盘，MiniApp 负责展示与分享，原始 Excel 只留档。

- 交付单位：`project-reports` 专家包（人设 + 技能 + 导入脚本 + MiniApp 模板 + 文档样例），**不是**一个孤立模板。
- 数据模型：`main`（任务快照）+ `period_reports`（导入记录）；项目是列，不是应用。
- 导入：确定性脚本做解析/归一化，Agent 调 `app_import_rows` 入库，`upsert_key` 保证幂等。
- 平台：P0 零核心改动；只把“多文件导入 + 来源 + 去重”等通用原语留到第二个垂直场景出现后再平台化。
- 节奏：先内部跑 10–50 份真实周报，再抽象 profile 交付试点，最后做 1.0.0 方案包。

### 实施进度（2026-11-19）

- ✅ 专家包骨架：`manifest.json`、`SOUL.md`、`AGENTS.md`、三个技能
- ✅ MiniApp 模板：`schema.json`、四页签 `app.html`、`preview.svg`
- ✅ 归一化脚本 `ingest.py` + 默认 profile + 表定义 + 使用说明
- ✅ 公开链接降级模式：公开 SDK 无 `aggregate` 时展示最新一期，筛选在前端完成，隐藏历史报告
- ✅ P1：`projects` 项目档案表、`pm-report-upgrade` 升级技能、`pm-report-archive` 归档技能与脚本
- ✅ 单元/契约测试：专家可发现、模板通过 `validate_html`、解析/归一化/幂等/失败行/PII/合并单元格/GBK
- ✅ 服务层集成测试：从模板建应用、添加 `period_reports` / `projects`、导入 CSV、重复导入走 upsert、升级保数据
- ✅ HTTP 端到端：从专家建 agent → 建应用 → 加表 → 导入 → upsert → 聚合 → 发布公开链接；公开行可读、`period_reports` 返回 404
- ✅ 端到端输入覆盖：CSV 样例 + 带标题行/合并单元格/合计行的真实 xlsx；两个报告期、5 条任务、重导 5 条更新
- ✅ 归档端到端：主表导出 → 拆分（保留 2 / 归档 3）→ dry-run replace → 正式 replace；公开行随实时数据变为 2
- ◐ 待办：用真实 Excel 跑 M0 验收；补 `projects` 表、升级技能、按年归档
- 未做：平台批量导入 API/UI、`contains` 搜索、主表显示名（P2，按触发条件再评估）

---

## 1. 目标与验收

| 阶段 | 验收标准 |
|---|---|
| 内部自用 | 1–2 名项目经理、20–50 份真实 Excel；解析成功率 ≥ 98%；10 份文件导入 < 2 分钟；重导不重复；公开链接无 PII/路径泄露 |
| 试点交付 | 第二家企业只替换 `profile.json` 即可跑通，不改代码 |
| 标准产品 | 方案包 1.0.0 可安装；1.0 → 1.1 升级保留数据；zh/en；Windows CI 通过；`make all` 绿 |

---

## 2. 范围

### P0（必须）

- 专家包 `project-reports`：人设、三个技能、导入脚本
- MiniApp 模板：`main` 任务表 + `period_reports` 表 + 四页签 HTML
- 导入引擎：profile 解析、字段归一化、派生字段、dry-run、幂等
- 文档、样例、黄金文件测试
- 内部真实数据验收

### P1（已落地）

- `projects` 维度表、项目档案维护（`project_name` / `owner` / `status`）
- 升级技能 `pm-report-upgrade`：补表、补列、换 HTML，不删数据
- 归档技能 `pm-report-archive` + `archive.py`：导出拆分、dry-run、二次确认后 replace
- 公开链接与移动端打磨（基础版）
- 方案包版本与 `CHANGELOG.md`（待发布时补）

### P2（平台化，需触发条件）

- 平台“文件夹批量导入”API/UI
- 导入来源列 + hash 去重成为平台能力
- 行查询 `contains` 过滤
- 主表显示名（`main` → “任务明细”）

### 明确不做

- 不做审批流、不做多页面 HTML、不做跨应用共享表
- 不把“项目/周期/任务”写进平台核心
- 不做公开市场分发（先企业内置 / 试点快照）

---

## 3. 总体架构

```text
用户（PM）
  │ 对话：初始化台账 / 导入本期报告 / 看风险
  ▼
专家：项目台账助手
  ├─ skills/pm-report-init     建应用、加表、配能力
  ├─ skills/pm-report-ingest   扫描 → 解析 → dry-run → 入库
  └─ skills/pm-report-review   趋势/风险复盘（只读）
  │
  ├─ 脚本 ingest.py（确定性，不碰 DB/HTTP）
  │    输入：reports/*.xlsx + profile.json
  │    输出：tasks.csv、period_reports.csv、manifest.json
  │
  └─ app_import_rows（平台工具）
        ├─ main（tasks）        upsert: project+period+task_id
        └─ period_reports       upsert: batch_id
  ▼
MiniApp：项目报告台账
  ├─ 本期任务 / 项目总览 / 历史报告 / 风险清单
  └─ 公开只读链接（仅 main 表）
```

数据流原则：

- 原始 Excel 留在 `reports/`，永不覆盖；不入库、不改名。
- 结构化数据进 MiniApp；来源文件路径只存在私有的 `period_reports` 表，不进公开表。
- 专家负责“灌数据、做复盘”，MiniApp 负责“看数据、筛数据、分享”。

---

## 4. 交付形态

### 4.1 方案包结构

```text
src/octop/infra/agents/experts/library/project-reports/
  manifest.json
  SOUL.md
  AGENTS.md
  skills/
    pm-report-init/SKILL.md
    pm-report-ingest/
      SKILL.md
      scripts/ingest.py
      references/profile.default.json
      references/README.md
      samples/...
    pm-report-review/SKILL.md
  app_templates/
    project-reports/
      schema.json
      app.html
      preview.svg
  docs/
    使用说明.md
    安装与升级.md
```

### 4.2 为什么是这个形态

| 形态 | 能带技能/模板 | 专家库可见 | 可随产品升级 | 判断 |
|---|---|---|---|---|
| 内置专家方案包 | ✅ | ✅ 内置标签 | ✅ | **推荐** |
| 用户发布的专家快照 | ✅ | ✅ 用户发布标签 | ❌ 私有副本不自动升级 | 试点/定制过渡 |
| 单独 MiniApp 模板 | ❌ 只有 schema+HTML | — | ✅ | 空壳，不能导入 |
| 单独技能包 | ✅ 只有技能 | — | — | 无界面，不可交付 |
| 丢进 `expert_market/` | ✅ | ❌ 目录只列出内置专家 | — | 不适合标准交付 |
| 写进平台内核 | — | — | ✅ | 垂直语义污染核心 |

关键事实：

- `create_from_template` 只复制 schema + HTML + 预览 + services，**不带数据、规则、能力、导入预设**（`infra/apps/service.py`）。
- 已发布专家快照只导出根 markdown、`skills/**/*`、`agents/*.md`，以及 `app_templates/` 下的 `schema.json` / `app.html`；`preview.svg`、`services.json`、`profiles/`、`samples/` 不会导出（`infra/agents/experts/publish.py`）。
- 因此 **profile 和样例必须放在 `skills/pm-report-ingest/references/` 内**，这样两种交付形态都能带上。

### 4.3 安装路径

- **内部自用**：专家发布为“已发布专家快照”，安装私有副本。
- **企业标准版**：方案包随企业构建进 `experts/library/`，升级随产品版本走。
- **试点客户**：发布专家快照安装；明确告知副本不自动升级。
- 不新增顶层菜单；应用出现在工作台/桌面。

---

## 5. 数据模型

### 5.1 主表 `main`（任务明细）

模板的 `schema.json` 只能定义主表，因此 **`main` = tasks**。HTML 中主表 key 固定为 `"main"`，不要写成 `"tasks"`。

| 列 | 类型 | 说明 |
|---|---|---|
| `project` | string, required | 项目 |
| `period` | string, required | `2026-W46` / `2026-11-19`，零填充可排序 |
| `task_id` | string, required | 稳定标识；缺失时用 `project + task_name` |
| `task_name` | string, required | 任务名 |
| `owner` | string | 负责人 |
| `status` | enum | `not_started / doing / blocked / done` |
| `progress` | integer | 0–100 |
| `priority` | enum | `low / medium / high` |
| `start_date` | date | |
| `due_date` | date | |
| `health` | enum | `on_track / at_risk / delayed`，导入时预计算 |
| `days_late` | integer | 预计算，未逾期为 0 |
| `notes` | text | |
| `batch_id` | string | 溯源键，不含路径 |

主表默认公开，公开链接只暴露这张表。`source_file` / `source_hash` 不放在这里，避免泄露目录结构。

### 5.2 `period_reports`（导入记录，私有）

初始化技能通过 `app_add_table(key="period_reports", label="导入记录", public=False, columns=[...])` 创建：

| 列 | 类型 | 说明 |
|---|---|---|
| `batch_id` | string, required | `sha256(source_hash + ":" + period)[:12]` |
| `project` | string | 单项目文件填；组合文件留空 |
| `period` | string, required | |
| `period_type` | enum | `daily / weekly / monthly` |
| `source_file` | string | 工作区路径 |
| `source_hash` | string | 文件 SHA-256 |
| `row_count` | integer | 解析出的任务行 |
| `imported_at` | datetime | |
| `status` | enum | `imported / partial / failed` |
| `error_summary` | text | 失败行摘要 |

### 5.3 `projects`（P1 已落地）

`project`、`project_name`、`owner`、`status`、`start_date`、`end_date`、`notes`。

私有表，由初始化/升级技能创建。页面读取它来显示项目正式名称、负责人和项目状态；
表里没有对应行时，直接用任务里的 `project` 值。项目列表仍从 `tasks` 聚合，
`projects` 只补充元数据，不参与导入流水线。

### 5.4 幂等与快照

- 任务 upsert 键：`project + period + task_id`
- 导入记录 upsert 键：`batch_id`
- 每期文件是快照：不同周期追加，不覆盖旧周期
- 同一期重导：更新该期任务，不产生重复
- 已知限制：修正文件删掉的任务不会自动删除旧行。P1 再考虑“按周期替换”或版本化 batch；P0 在文档中说明，必要时手工删行

### 5.5 容量

- 单表上限：20,000 行、5 MB、50 列（`infra/apps/models.py`）。
- 单 PM 3–5 项目 × 20 任务 × 52 周 ≈ 5,000 行/年，够用。
- 部门级 20 项目 × 30 任务 × 52 周 ≈ 31,000 行/年，超限；P1 做按年归档（导出 CSV 后清理旧年，或按年建表）。

---

## 6. 导入契约

### 6.1 Profile

默认放 `skills/pm-report-ingest/references/profile.default.json`；客户可覆盖为 `reports/import-profile.json`。

```json
{
  "version": 1,
  "file_pattern": "^(?P<project>.+)[-_ ](?P<period>\\d{4}-W\\d{1,2}|\\d{4}-\\d{2}-\\d{2})\\.(?:xlsx|xlsm|xls|csv)$",
  "sheet": "任务明细",
  "header_row": 2,
  "columns": {
    "任务编号": "task_id",
    "任务名称": "task_name",
    "负责人": "owner",
    "状态": "status",
    "进度": "progress",
    "优先级": "priority",
    "开始日期": "start_date",
    "截止日期": "due_date",
    "备注": "notes"
  },
  "status_map": {
    "未开始": "not_started",
    "待办": "not_started",
    "进行中": "doing",
    "处理中": "doing",
    "阻塞": "blocked",
    "已完成": "done"
  },
  "priority_map": {
    "低": "low",
    "中": "medium",
    "高": "high"
  },
  "task_id_fallback": ["project", "task_name"],
  "date_formats": ["%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"],
  "health_rules": {
    "at_risk_days": 3,
    "delayed_statuses": ["blocked"]
  }
}
```

`header_row` 与平台 importer 一致，使用 0-based。

### 6.2 脚本 CLI

```text
python skills/pm-report-ingest/scripts/ingest.py \
  --input reports/ \
  --profile reports/import-profile.json \
  --out reports/normalized/
```

输出：

- `reports/normalized/tasks.csv`
- `reports/normalized/period_reports.csv`
- `reports/normalized/manifest.json`（文件、hash、行数、告警、失败行）

退出码：

- `0`：成功
- `2`：有校验失败但可部分导入
- `3`：没有可导入文件

脚本只做解析与归一化，不调用 API、不写 DB。

### 6.3 Agent 导入流程

1. `app_read_data(table="period_reports", limit=500)` 读已知 hash（一年约 52 条，一页够）。
2. 扫描 `reports/` 或用户指定的 `inbound/` 文件，跳过已导入 hash。
3. 跑脚本；展示 dry-run 摘要。
4. 有失败行或 PII 被拦截时，先让用户确认。
5. `app_import_rows(paths=["reports/normalized/tasks.csv"], table="main", dry_run=true, upsert_key=["project","period","task_id"])`。
6. 正式导入 tasks；再导入 `period_reports`，`upsert_key=["batch_id"]`。
7. 回报新增/更新/失败/跳过；提示打开应用或分享链接。

### 6.4 错误与 PII

- 缺 `task_name`、日期非法、进度非数字 → 失败行，不阻断其它行
- 状态无法映射 → 归入失败行，提示补 `status_map`
- 默认 `include_pii=false`；薪资/手机号等列被丢弃，必须用户显式确认
- 文件大小 ≤ 50 MB；行数上限按表容量预检

---

## 7. MiniApp 设计

### 7.1 模板限制

`create_from_template` 只创建主表（`infra/apps/service.py`），因此：

- `schema.json` = `tasks`
- 初始化技能用 `app_add_table` 加 `period_reports`（P1 再加 `projects`）
- HTML 中主表 key 固定为 `"main"`

### 7.2 页面

| 页签 | 行为 | SDK |
|---|---|---|
| 本期任务 | 默认最新 period；项目/状态/负责人筛选；分页 | `listRows({table:"main", sort:"-period", limit:1})` + 过滤 |
| 项目总览 | 按项目聚合任务数、完成率、延期数、阻塞数 | `aggregate` 多次调用 + 缓存 |
| 历史报告 | 按 period 倒序列出导入记录与行数 | `listRows({table:"period_reports"})`，公开页隐藏 |
| 风险清单 | `health=delayed` 或 `status=blocked`；按项目分组 | `listRows` 等值过滤 + 前端合并 |

- 任务详情抽屉：任务轨迹（`filters:{project, task_id}, sort:"period"`）、来源文件（登录态才显示）
- 单次最多读 500 行；超过就分页/服务端过滤
- 多选状态：本期行通常 < 500，前端过滤
- 只读公开页：不请求 `period_reports`，隐藏来源/历史；写操作 try/catch 后隐藏
- 移动端：页签可横向滚动，表格卡片化

### 7.3 能力位

页面只读，初始化时只给：

```text
data.read
```

任务更新通过重新导入源 Excel 完成，页面不需要 `data.write`。`ai.generate`、
`external.fetch`、`events.emit`、`game.launch`、`chat.compose` 默认关闭；将来加“交给助手”
按钮时再按需开启 `chat.compose`。

### 7.4 公开链接

- 主表 `main` 默认公开；`period_reports` 私有
- 公开页只读取 `main`，不显示来源文件、导入时间、失败摘要
- 公开 SDK 目前只有 `schema` / `tables` / `listRows` / `asset`，没有 `aggregate`，且
  `listRows` 不接受 `filters` / `sort`。页面检测到缺少 `aggregate` 时进入公开降级模式：
  读取最后最多 4 页（≤2000 行），只展示最新一期，项目/状态/负责人筛选在前端完成，隐藏历史报告。
  如果以后需要公开页的趋势和服务端筛选，再评估给公开 rows 加 `filters`/`sort` 与
  `aggregate`（P2，平台通用能力）。
- 发布/撤销走平台现有公开链接能力（`app_share_link`）

---

## 8. 专家包

### 8.1 manifest

`manifest.json` 关键字段：

- `id`: `project-reports`
- `label`: 项目台账助手 / Project Ledger Assistant
- `welcome_message`: 说明“导入周报、看历史、查风险”
- `icon_name` / `color`
- `prompt_files`: `SOUL.md`、`AGENTS.md`
- `quick_prompts`：
  1. 初始化台账
  2. 导入本期报告
  3. 看本周风险
- `task_examples`: 周报导入、风险复盘等

### 8.2 初始化流程

1. `app_create_from_template(template="project-reports", source="agent", name="项目报告台账")`
2. `app_add_table(key="period_reports", label="导入记录", public=False, columns=[...])`
3. `app_set_capabilities([...])`
4. 确保 `reports/` 存在；说明文件命名规范
5. `app_open(app_id)` 让用户看到结果

### 8.3 技能

- `pm-report-init`：建应用、加 `period_reports` / `projects` 表、配能力、准备目录
- `pm-report-ingest`：扫描、解析、dry-run、入库、写导入记录
- `pm-report-review`：只读查询，输出风险/趋势摘要，不写数据
- `pm-report-upgrade`：补表、补可选列、替换 HTML；不删除/重建/清空数据
- `pm-report-archive`：读取用户导出的主表 CSV，拆分 retained/archived；二次确认后用
  `app_import_rows(mode="replace")` 清理活动表

---

## 9. 平台改动

P0 **零核心改动**。仅两个可选小项，等真实需要再做：

1. 主表显示名：模板 schema 增加可选 `table_label`，或加 `app_set_table_label` 工具；否则数据页显示 `main`。
2. 批量导入通用原语：来源列/hash 去重/按文件覆盖 header；触发条件是第二个垂直场景也需要。

`contains` 搜索留到试点后验证，不提前做。

---

## 10. 里程碑

| 阶段 | 时间 | 交付 | 出口条件 |
|---|---|---|---|
| M0 方案定型 | 第 1 周 | 10 份真实文件、profile 草案、黄金样例 | ≥98% 行可解析；任务标识规则确定 |
| M1 引擎 + 模板 | 第 2–3 周 | `ingest.py`、schema、四页签 HTML、单测 | 本地 10 份文件端到端通过；重导幂等 |
| M2 专家 + 内测 | 第 4–5 周 | 专家包、三个技能、E2E 流程 | 1–2 名 PM 连续使用 2 周；无 P0 缺陷 |
| M3 产品化 | 第 6–8 周 | 1.0.0 方案包、文档、升级技能、试点 profile | 第二家只换 profile 跑通；Windows CI 绿 |
| M4 平台化 | 触发后 | 批量导入 API/UI、contains、主表名 | 第二个垂直场景复用；不引入垂直语义 |

---

## 11. 测试计划

- **单元**：profile 解析、状态映射、日期格式、派生字段、失败行、PII 跳过
- **黄金文件**：干净周报、标题行、状态同义词、缺 task_id、缺必填、合计行、多 sheet、重复导入、修正导入
- **集成**：`create_from_template` → `app_add_table` → 导入 CSV → 断言行数/更新数/公开面脱敏
- **前端**：jsdom 加载 `app.html` + mock `octopApp`，断言四页签与空状态
- **跨平台**：Windows 路径、CRLF、UTF-8/GBK CSV
- **命令**：`uv run pytest tests/unit/apps tests/unit/experts -q`；最终 `make all`

---

## 12. 升级与分发

- 方案版本写在包目录和 `CHANGELOG.md`；不依赖 manifest 未支持字段。
- 内置专家升级只影响新建 agent；已有 agent 用 `pm-report-upgrade`：
  1. 对比 `tables.json` 与当前表，补 `period_reports` / `projects`
  2. 对比 `schema.json`，用 `app_update_schema` 追加缺失的可选列
  3. `app_write_html` 换界面（覆盖前确认）；数据保留
- 试点客户：发布专家快照安装；明确告知副本不自动升级。
- 企业标准版：方案包随企业构建进 `experts/library/`；不走公开市场。

### 归档

- owner/admin 从应用详情页导出主表 CSV 并下载一次应用备份，放到 `reports/archive/`。
- `archive.py --input <export.csv> --keep-from 2026-W01` 输出：
  `tasks_retained.csv`、`tasks_archived.csv`、`archive_summary.json`。
- 只归档不修改应用是默认路径；需要清理活动表时，先 dry-run，核对
  `would_import == retained`，用户二次确认后再 `mode="replace"`。
- 原始 Excel、导出 CSV、归档 CSV 都不删除。

---

## 13. 风险

| 风险 | 对策 |
|---|---|
| Excel 格式不统一 | profile + 黄金测试；v1 要求表格化 sheet；复杂表单先人工规范 |
| 任务标识不稳定 | 优先 `task_id`；没有就用 `project+task_name` 并在导入报告里提示改名风险 |
| 修正文件导致旧任务残留 | P0 文档说明；P1 做按周期替换/版本化 |
| 容量超 2 万行/年 | 监控行数；P1 按年归档 |
| Agent 不按技能执行 | 脚本确定性 + 明确 checklist + dry-run 强制 |
| 公开链接泄露路径/PII | 溯源只放私有表；PII 列标记；公开页只读 main |
| 主表显示 `main` | P2 小改动；不阻塞 P0 |
| 试点快照丢文件 | profile/samples 放 `skills/**` 内 |

---

## 14. 待确认问题

1. 文件是“每期一个组合文件”，还是“每项目每期一个文件”？
2. 是否有稳定的任务编号？没有的话任务改名如何处理？
3. 是否需要保留每周任务快照，还是只要最新状态？
4. 每个文件是否包含多个 sheet（汇总/任务/风险）？
5. 哪些列属于 PII（姓名、薪资、手机号）？
6. 主周期是周报还是日报？决定默认 `period_type` 和命名展示。

---

## 15. M0 输入清单

开始 M0 前需要业务方提供：

- 10 份脱敏后的真实周报 Excel（覆盖正常、异常、缺列、多 sheet）
- 文件名与项目/周期的对应关系
- 任务编号是否稳定、状态值全集、进度口径
- 期望的“本期任务 / 项目总览 / 风险清单”样例
- 哪些列属于 PII、公开链接给谁看

拿到后先在 `reports/normalized/` 产出样例 CSV 和失败行报告，再动模板与技能。

---

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-19 | 初稿：交付形态、数据模型、导入契约、四页签、里程碑与验收 |
