# Octop 快速交付场景与落地计划（零售经营方向）

> 依据：`docs/retail-scenario-gap-analysis.md`（差距分析）
> 日期：2026-11-16
> 原则：**零 DB 迁移、零新平台子系统**。本轮所有新增表都是 miniapp 的表，新增代码只有一处（3C 的 ffmpeg 剪辑工具）。

---

## 0. 已定决策（2026-11-16 讨论定稿）

| 议题 | 决策 | 影响 |
|---|---|---|
| 3B 经营简报载体 | **两者都要**：IM 推纯文本（结论与数字）+ 链接跳 board（待办与按钮） | 工期 5 天；PDF 的"不做独立 App"理解为"不装客户端" |
| 3C 剪辑工具落点 | **Octop 内置 connector adapter**（`infra/connectors/gateway/adapters/` 新增模块 + `catalog.json` 一行） | harness 是外部 PyPI 依赖改不了；adapter 形态同时是 Agent 工具、可被 `light_app_services` 绑定 |
| 2B' 会前准备触发 | **自然语言触发**（IM 里说"准备一下张三的会前材料"） | 零代码、零客户配合；不做会议时间表 + cron |
| 3A 话术库维护方 | **总部统一维护** | 沿用现有 `owner` / `shared` 机制，**不动 KB 权限**（省 1–2 天） |

### 讨论中修正的三处（原计划不准确）

1. **3A 商品表不能直接查**：`app_read_data` 一次上限 500 行（`infra/apps/models.py:45` `MAX_ROW_READ`），
   且**没有按列过滤**，只有 `limit` + `offset` 分页 → 必须有分流规则（见 §1 第二批 3A）。
2. **1.3 分层不能写回客户表**：客户表由 Excel `append` + `upsert_key` 导入，
   **下次导入会用源文件覆盖分层结果** → 分层结果存独立表。
3. **2B' 的"最近投诉"取不到**：没有工单系统，且 IM 身份塌缩到属主，取不到按客户归集的历史
   → 唯一路径是 Excel 里加一列"备注/投诉"，导入时映射进客户表。

---

## 0.1 一句话结论

**先用「客户导出 Excel」绕开 ERP 直连，把 1.1 从"不可交付"变成"半天可交付"，**
四张标准表一落地，**3A 销售支持 / 2B' 会前准备 / 3B 经营简报 / 2A 经营诊断 / 1.3 分层**
五个下游场景全部解锁 —— 而它们**全部可以用现有的 miniapp + cron + 知识库 + IM 通道拼出来**。

真正该往后放的只有三个：ERP 直连、屏幕与会议读取、事中实时提示。

---

## 1. 场景筛选结果

### 第一批 · 配置即交付（0.5 天）

| 场景 | 为什么能快 | 交付物 |
|---|---|---|
| **0.3 外部搜索** | harness 内建 5 家 `web_search` + `web_fetch`（`tool_catalog.py:72-76`），已通；只差配 provider 与提示纪律 | 一个能查政策 / 同行 / 价格并附来源的 Agent |

配置：2 个 provider（对应 PDF 的"博查 + 智谱"）；SKILL.md 写死两条规则 ——
**搜不到就说"我没找到"**、**每条结论附来源站点**。

### 第二批 · 现有能力拼装（3–5 天/项，无新表、无新平台子系统）

**共用前置（半天，所有下游都靠它）**

```
一个 miniapp「门店经营」+ 4 张表：订单 / 库存 / 客户 / 商品
客户导出 Excel/CSV
  → POST /api/apps/{app_id}/upload                     （落到 workspace inbound/imports/）
  → POST /api/apps/{app_id}/tables/{key}/import        （mapping / mode / upsert_key / dry_run 全支持）
  → light_app_sources(kind=workspace_file, schedule_seconds=N) 夜间自动同步
```

> 这条链**完全现成**（`api/routers/apps.py:2940-3040`），导入零代码。
> `mapping` 就是 PDF 说的"字段语义映射"——只是这里由人/AI 一次性给出，而不是写成通用识别引擎。

| 场景 | 交付物 | 关键机制 | 工期 |
|---|---|---|---|
| **2B' 会前准备** | 开会前 30 分钟推一页纸：客户历史成交价 / 账期 / 最近投诉 / 预判异议 | **自然语言触发**（IM 里说"准备一下张三的会前材料"）→ 读订单表 → 生成 → 回在同一 IM 会话（零代码）；"最近投诉"取自客户表 Excel 的备注列 | **3 天** |
| **3A 销售支持（话术版）** | 店员在企微/微信同窗口问到店里的真实商品与标准话术 | 商品表走 miniapp；**≤500 行直接 `app_read_data`，>500 行 `GET /{app_id}/export` 导 CSV 后 grep**；四类异议话术进知识库（总部统一维护）+ SKILL 规定"先查表→再查话术→都没有就说问店长" | **3 天** |
| **3B 经营简报** | 老板手机端每天早上收到：结论—数字—异常—待办 + 数据来源脚注 | cron 跑批 → 写"简报"表 → **IM 推纯文本 + `app_share_link` 跳 board**（两者都要） | **5 天** |
| **1.3 分层（订单口径）** | 铁杆 / 潜力 / 沉睡 / 走 四档标签 | 夜间跑批读订单表现算 → 写**独立的「分层结果」表**（不写回客户表，会被下次导入覆盖） | **2 天** |

**这批共同做到的事**：3A、3B、2A、1.3 需要的"四张标准表"一次到位，之后都是往表里读、
往表里写，不需要再动数据层。

### 第三批 · 需要少量新代码（1–2 周）

| 场景 | 新增东西 | 工期 | 已知局限 |
|---|---|---|---|
| **2A 经营诊断与归因** | 指标口径写进 SKILL/配置；归因叙述写"诊断"表 | 5–7 天 | miniapp 表**无公式列** → 派生指标（毛利/周转）由模型算后写回，有算错风险。缓解：口径固化为"表+列+公式"，数字一律从表读，不让模型心算 |
| **3C 营销短视频（模板剪辑版）** | ① ffmpeg 依赖 ② **Octop 内置 connector adapter**（`infra/connectors/gateway/adapters/` 新增模块，按 `http_extract.py` 的 `TOOLS`/`list_tools()`/`call_tool()` 三段式暴露 `render_video`，注册进 `catalog.json`）③ 行业模板 SKILL | 1–2 周 | 分发只接公众号草稿；到店归因（优惠码 → `external_events` → attribution 表）可独立并行，不依赖剪辑能力 |

### 本轮明确不做

| 不做 | 理由 | 何时再做 |
|---|---|---|
| **1.1 ERP 直连** | 技术不是难点，**每家客户表结构映射的人工成本才是**。先用导出文件绕开 | 有 1–2 个真实客户、确认要每天自动同步后，再上 SQL Server 只读 adapter（+1 周）；注意 `infra/apps/sources.py:1-7` 限定数据源只吃进程内 gateway 连接器 |
| **1.2 屏幕与会议读取** | 需要"用户本机屏幕 / 麦克风上行 + 持续感知触发器"，本轮风险最高 | 阶段 3；零件已齐（`desktop_screenshot` 落 workspace + `steer` 可注入进行中的一轮），差装配 |
| **2B 事中实时提示** | 依赖 1.1 与触发器 | 先用 2B' 会前准备交付价值 |
| **0.2 平台监测** | PDF 自己判为客户级定制且法律风险高 | 若有客户明确付费，按"客户级数据定制"单独立项，不进平台 |
| **contacts 跨平台身份归并** | 是"1.1 之后的第二块地基"，但 1.3 的**分层**不依赖它（只依赖订单表） | 第二个客户接入 IM 时再做 |

---

## 1.1 逐场景措施要点（讨论结论落地）

### 0.3 外部搜索
1. Settings → Advanced → Search 配 2 个 provider（Tavily 偏英文通用，Kimi 偏中文、能覆盖企查号/知乎/站外转载内容）→ 跑 10 题后留 2 个。
2. 装 SKILL.md `search-policy`：政策类 / 同行类 / 价格类三种问法各写死流程 —— 先搜 → 引用来源站点 → 搜不到明说"我没找到"并建议问谁。
3. **默认不自动搜**，只在识别到"查一下 / 现在还能办吗 / 多少钱"这类外部事实意图时才搜（否则每轮都搜会拖慢并引入噪声）。

### 2B' 会前准备
1. 数据：客户表 + 订单表。
2. 触发：自然语言，SKILL 里写死触发语句与动作（查客户 → 查订单 → 算历史成交价 / 账期 / 最近到店 → 生成一页纸 → **回在同一 IM 会话**）。零代码。
3. 载体：IM 文本为主；同时落一份 workspace HTML + `app_share_link` 链接（好看，但 IM 内是 web-view 不是原生卡片）。
4. **"最近投诉"只来自客户表 Excel 的备注列** —— 需在交付清单里向客户说明并要求导出时带上这一列。
5. 新客户（无订单记录）必须明说"这位是新客，没有历史成交价"，不许用同类客户的数据凑。

### 3A 销售支持
1. 商品 / 价格 / 库存 → miniapp 商品表（Excel 导入），**绝不进知识库**。
2. **检索分流（写进 SKILL）**：
   - ≤500 行 → `app_read_data` 直接读；
   - >500 行 → `GET /{app_id}/export?table=商品`（`api/routers/apps.py:2170`）导 CSV 落工作区 → `execute` + grep；
   - 要求毫秒级且表极大 → 第二版再考虑 `light_app_services` 绑外部只读数据源。
3. 四类异议话术 → 知识库 4 篇，每篇写死"触发条件 + 标准应答 + 依据哪张表哪个字段"。总部统一维护。
4. SKILL 顺序：先查商品表拿实时价与库存 → 再检索话术库 → 都没有 → "这个我不确定，问店长"。
5. **指代歧义（"这个多少钱"）一律反问**，不猜最近提及的商品 —— 猜错价格对店员是硬伤。
6. IM 交付：企微 Markdown + 一张商品图；微信纯文本 + 图。
7. "新人识别"本轮砍掉（依赖 contacts 层）。

### 3B 经营简报
1. "简报"表（miniapp 表，一行一条简报，按日期 upsert）。
2. 跑批：`cron:0 7 * * *` + `task_type=agent` + SKILL「经营简报」→ 读订单 / 库存表 → `app_append_row` 写简报表 → 同时输出纯文本 → cron 自动推 IM。
3. 四段式用 SKILL 硬约束（结论 / 数字 / 异常 / 待办），每段都要有来源行；底部脚注写"数据源 + 同步时间 + 只读声明"（**同步时间从源文件 mtime 取**，`app_services` 没有 last-synced 字段）。
4. 待办动作：简报行带 `action_type`（改价 / 生成话术 / 打电话）→ 映射到 `app_update_row` / `generate` / `tel:` 拨号链接；**"打电话"要求客户表有手机号列**。
5. **载体两者都要**：IM 纯文本给结论与数字（可直接读）+ `app_share_link` 跳 board 看待办与按钮。
6. **"一键改"第一版只生成待办并推给对应人，不直接改价**（合规与可回滚）。
7. 埋点：open / click / adopt 三事件写 miniapp 小表（带 user_id + ts），不新建表。

### 1.3 客户分层
1. 夜间跑批（**与 3B 同一个 cron 回合**，不另起任务）读订单表现 → 算档位 → 写**独立的「分层结果」表**（`customer_id` + 档位 + 依据 + 计算时间）。
2. **不写回客户表**（会被下次导入覆盖）。
3. 口径用 PDF 的四档名（铁杆 / 潜力 / 沉睡 / 走），每档写死判据，不用 RFM 字样。
4. 本轮只用订单数据（快、可算）；"沟通记录"维度等 contacts 层。

### 2A 经营诊断与归因
1. **指标口径写进 SKILL**，逐条写死"从哪张表的哪列、怎么算"（例：毛利 = 订单表 `成交额` 之和 − 订单表 `成本单价` × `数量` 之和）。
2. 跑批算指标 → 写「指标」表（期间、指标名、值、口径、计算过程）。
3. 归因叙述写「诊断」表（结论 + 证据行 + 置信 + 是否异常）。
4. 异常阈值入配置，先做"环比波动超阈值就标注"。
5. **最大质量风险不是工程，是模型算派生指标**（miniapp 表无公式列）→ 口径写成可执行步骤而非让模型自由发挥；诊断表存"计算过程"字段供抽查复算。

### 3C 营销短视频
1. 加 ffmpeg 依赖（当前无）。
2. **剪辑工具做成 Octop 内置 connector adapter**：`infra/connectors/gateway/adapters/` 新增模块，按 `http_extract.py` 的 `TOOLS` + `list_tools()` + `call_tool()` 三段式暴露 `render_video(template, inputs, output)`，注册进 `catalog.json`。
3. 行业模板做成 SKILL.md（模板结构 + 变量约定 + 调用参数）；素材走 workspace（已有产物白名单机制 `desktopFiles.ts:20-27`）。
4. 分发只接公众号草稿（现有能力），其余人工导出。
5. 到店归因（优惠码 → `external_events` → attribution 表）**可独立并行**，不依赖剪辑能力。

---

## 2. 里程碑与验收

| 周 | 交付物 | 验收（照抄 PDF 的 KPI 口径） |
|---|---|---|
| **W0** | 标准四表 + Excel 导入跑通（1 个真实客户） | 一份客户导出文件 → 4 张表 → 能回答"上周卖了多少、哪个 SKU 库存低" |
| **W1** | 3A 话术版 + 2B' 会前准备 | 销售在企微问 8 类问题中 ≥6 类答得出且带店里的数；会前一页纸准时送达 |
| **W2** | 3B 简报版 + 埋点 | 老板 7 天内 ≥5 天打开；待办点击率可测；来源脚注正确 |
| **W3** | 2A 诊断 + 1.3 分层 | 能回答"本月毛利掉了，是价涨还是量跌"，且结论能指出依据行 |
| **W4** | 3C 剪辑模板 | 30 秒素材 → 3 分钟出片；素材来自门店自有 |

**埋点落点**：3 个事件（`open` / `click` / `adopt`）记进 miniapp 的一张小表（带 user_id + ts）。
不新建 `briefing_events` 表 —— miniapp 表够用，且省一次迁移。

---

## 3. 三条必须写进 SKILL 的"反幻觉"规则

1. **答不出就说不知道**：3A 里"先查商品表 → 再查话术库 → 都没有就说'这个我不确定，问店长'"。
   没有这条，知识库没覆盖的问题模型会编（PDF 2.3 对外部搜索也是同一结论）。
2. **数字只从表里读**：毛利、库存、成交价一律来自 miniapp 表的读取结果，模型只做减法叙述，不允许凭记忆或估算。
3. **数据源不新鲜要显式说明**：跑批前检查源文件 mtime；缺失或过期时在简报里明写"数据源未更新（最后同步 X）"，
   不允许拿旧数据当今天的数据讲。

---

## 4. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 客户不导出 / 忘导出 | 数据不新鲜，2A 结论失真 | 把"每天导出"写进交付约定；跑批前校验 mtime，缺失时简报显式声明（见上第 3 条） |
| 派生指标由模型计算 | 毛利算错，老板照错数字决策 | 口径固化为"表 + 列 + 公式"写进 SKILL；关键指标在简报里附原始行数供抽查 |
| 3A 话术库覆盖不足 | 店员问什么答什么，等于没有 | SKILL 强制兜底话术（"问店长"）；把未命中问题记进 miniapp 表，周会上补话术 |
| 落到 light_app 表的数据被当成"应用数据" | 后续做 1.1 直连时要迁移 | 表结构按标准四表设计并写进文档，直连时用 `mapping_json` 复用同一套列名 |

---

## 5. 若只能做一件事

**做 W0 的"标准四表 + Excel 导入"**（半天）。

理由：它是 3A / 3B / 2A / 2B' / 1.3 的共同前置，且**一行代码都不用写** ——
`upload` + `import` 两个 HTTP 端点已经够用；而跳过它去做任何下游场景，都会在第一次接入客户数据时卡住。

---

## 附：本计划依赖的现成能力索引

| 能力 | 位置 |
|---|---|
| 文件上传 → 工作区 | `api/routers/apps.py:2940` `POST /{app_id}/upload` |
| 带映射导入（append/replace、upsert_key、sheet、header_row、dry_run） | `api/routers/apps.py:2969` `POST /{app_id}/tables/{table_key}/import` |
| 同能力的 Agent 工具版 | `infra/apps/tools.py:1188` `app_import_rows` |
| 解析 Excel/CSV/JSON（含表头行偏移、合计行剔除） | `infra/apps/importer.py:61` `parse_import_file` |
| 工作区文件定时同步 | `light_app_sources` 的 `workspace_file` kind + `schedule_seconds`（`infra/db/migrations/025_light_apps.sql:68-79`） |
| 只读数据绑定 + 缓存 + 限流 | `infra/db/repos/app_services.py:31-46`（`allowed_tools` / `allowed_url_prefixes` / `cache_ttl_seconds`） |
| 图表（6 种 + sum/avg/count/min/max 服务端聚合） | `infra/apps/charts.py:20` |
| 防模型编数的只读助手 | `infra/apps/assistant.py` |
| cron 触发（cron:/interval:/date:/agently:） | `infra/cron/trigger.py:77` |
| cron 跑 Agent 回合 → 推 IM | `infra/cron/delivery.py:71,130` |
| IM 内发链接与预览图 | `infra/apps/tools.py:600-630,636` `app_share_link` |
| 知识库检索（向量） | `infra/knowledge/tools.py:15` `search_knowledge` |
| 技能（渐进触发的规则文件） | `infra/skills/`、`infra/agents/experts/library/.../SKILL.md` |
| 外部搜索 | `infra/agents/settings/tool_catalog.py:72-76`、`infra/utils/search_probe.py:20` |
