# 零售经营场景对 Agent 平台的需求 × Octop 现状差距分析

> 来源：`~/智总管产品功能分层调研汇报_v3.pdf`（2026-11，四层十模块调研汇报，54 页）
> 日期：2026-11-16
> 口径：**Octop 现状以代码为准**，文档结论仅作参考（`docs/miniapp-overview.md` 为 MiniApp 现状准绳）。
> 通道实现位于外部包 `octop-gateway`（`.venv/lib/python3.12/site-packages/octop_gateway/`），
> `src/octop/infra/gateway/` 只是宿主侧粘合层 —— 引用时两者都算 Octop 能力。

---

## 0. 一句话结论

Octop 是**通用 Agent 平台底座**，不是零售经营系统。它天然吃下 PDF 十模块里的
**连接层（0.1 部分 / 0.3 完全）** 与 **输出形态（3A、3B 的"形"）**；
**感知层（1.2）有全套零件、缺最后一段装配**；
**洞察层（2A、2B）与数据层（1.1、1.3）基本是空的** —— 这四块要建的是一层
「门店/零售业务契约」（标准业务表 + 指标层 + 客户档案 + 归因口径），不是平台能力。
文档里"没做过一次"的那几件（客户画像、经营归因、实时参谋、平台数据监测），
在 Octop 里同样一次都没做过。

---

## 1. PDF 场景清单（分层表实列 11 项）与平台需求提炼

> 注：PDF 标题作「四层十模块」，但其分层表实际列出 11 项（0.1–0.3 / 1.1–1.3 / 2A–2B / 3A–3C）。本文按表逐项评估。

| 层 | 模块 | PDF 定义（要点） | PDF 的选型结论 |
|---|---|---|---|
| 0 连接 | **0.1 IM 全通** | 多平台对话统一接入，AI 能读到、能回复、断点提醒人接手 | 必做 = 企微 + 公众号 + 飞书 + 钉钉；灰度 = 抖音/小红书私信；不做 = 微信个人号 |
| 0 连接 | **0.2 平台监测** | 盯客户自己的小红书/抖音/视频号/公众号号：粉丝、曝光、评论、私信 | 自建 = 公众号 + 抖音自有号授权；不做小红书；不做代运营 |
| 0 连接 | **0.3 外部搜索** | 查公开信息（政策、同行、价格），能给出处 | 必做 = 博查 + 智谱两家；不接阿里云；搜不到要说"没找到" |
| 1 感知 | **1.1 ERP 只读适配器** | 只读连客户 ERP，抽出订单/库存/客户/商品四表 | 直连（只读账号）或导出文件；CDC 增量；**不改数据** |
| 1 感知 | **1.2 屏幕与会议实时读取** | 开会/谈价时读屏幕、听对话，实时给建议 | 形态 = 桌面悬浮窗 + 麦克风（双通道）；核心 = 实时截屏 + 无障碍树/OCR |
| 1 感知 | **1.3 客户分层与档案** | 给客户打标签：多次没来、花了多少、潜力/顶/走 | 数据 = 订单 + 沟通记录；自己算，不要老模板 |
| 2 洞察 | **2A 经营诊断与归因** | "这个月毛利只有 1.2 万，是进货价涨了还是卖不动了" | 输出一个动作，不是张报表 |
| 2 洞察 | **2B 实时参谋** | 谈判/会议进行中给建议（报价对标 / 追问点 / 预判异议 / 方案优点） | 延迟 ≤2-3 秒（不是电话客服的 500ms）；国内"事后"居多，"事中"是空白 |
| 3 输出 | **3A 销售支持** | 店员当场能查能说出口；答案必须出现在同一个 IM 窗口 | 知识来自门店自己的产品库，不做独立 App |
| 3 输出 | **3B 经营驾驶舱** | 老板每天早上打开的一个页面：结论—数字—异常—待办 | 不做报表；每条建议带"一键改/生成/打电话"按钮；底部写数据来源 |
| 3 输出 | **3C 营销短视频** | 自有素材 + 行业模板 → 出片、批量分发、回答"带来几个到店客人" | 只做"素材+模板+到店归因"；不做一键成片、不做换脸 |

---

## 2. 评估总表

| 模块 | 满足度 | Octop 已有 | 核心缺口 |
|---|---|---|---|
| 0.1 IM 全通 | ⚠️ | 企微/飞书/钉钉/微信收发、群聊@我、HITL 审批、四条主动推送链路 | **公众号通道缺失**、**无外部联系人/身份归并**、**无转人工接管**、无平台历史回溯 |
| 0.2 平台监测 | ❌ | 无（仅浏览器 CDP 可 RPA 抓取，等 PDF 判定的"高风险"路径） | 无抖音/公众号自有号数据连接器；**建议降级为客户级交付** |
| 0.3 外部搜索 | ✅ | harness 内建 `web_search` 五家 + `web_fetch`（SSRF 防护）+ 设置页探活 | 仅需"答不出就说没找到 + 给来源"的提示纪律 |
| 1.1 ERP 只读适配器 | ❌ | Fernet 凭据加密、自定义 MCP、cron、**Light App 落表中间层** | **无 SQL Server 连接器**、无四表、无增量水位；venv 无 pyodbc/pymssql |
| 1.2 屏幕与会议读取 | ⚠️ | 截图进多模态（`browser_use`）、`desktop_screenshot`、腾讯实时 ASR、远桌面/浏览器 WS 流、rapidocr | **无用户本机屏幕/麦克风上行**、**无持续感知触发器**、无无障碍树、无飞书会议 |
| 1.3 客户分层与档案 | ❌ | 可用 light_app 表承载 | **无客户表/标签/分层模型**；知识库是文档 RAG 不能当档案；**IM 身份塌缩到属主** |
| 2A 经营诊断与归因 | ⚠️ | cron 夜间跑批、结果推 IM、服务端聚合防编数、trajectory 决策链 | **无指标层**、**无归因算法**、**无诊断产物表**、无 pandas |
| 2B 实时参谋 | ⚠️ | chat WS、**turn 内 steer 注入**、HITL、实时 ASR | **无秒级触发回路**（事件平面 300s 轮询）、单进程 WS hub 无跨机扇出、无历史成交价数据源 |
| 3A 销售支持 | ⚠️ | 知识库 + SKILL 话术 + 维护界面 + MiniApp 结构化数据 | 商品表进 RAG 会丢列语义；**IM 内无卡片**；无场景化话术检索器；**KB 无角色/门店 ACL**；无新人识别 |
| 3B 经营驾驶舱 | ⚠️ | MiniApp 卡片看板 + 6 种图表 + 行级动作 + SDK 11 能力位 | **无简报页面**、**无 cron 产物表**、无四段式模板、无来源脚注字段、**4 个 KPI 零埋点**、查询未下推 |
| 3C 营销短视频 | ⚠️ | `generate_video`/`generate_image`（三家 provider）、TTS、PPT/文档生成、事件平面 | **无 ffmpeg → 无剪辑/模板/字幕**、无素材库、无批量分发、**无优惠码/到店归因模型** |

---

## 3. 逐模块详评

### 0.1 IM 全通 — ⚠️ 部分满足

**Octop 现状**

- 10 个内置通道，**全部能读能写**：企微 aibot WebSocket（`octop_gateway/channels/wecom.py:67`）、
  飞书 WS 收 + REST 发（`channels/feishu.py`，支持 post/interactive）、钉钉 Stream 收 + OpenAPI 发
  （`channels/dingtalk.py`）、微信 iLink Bot 长轮询（`channels/weixin/api.py:39-41`）、QQ / Telegram /
  Discord / 元宝 / 小艺 / MQTT。
- 群聊语义完整：`GroupVisibility` / `GroupActivation` / `GroupHistoryMode`（`octop_gateway/group_context.py:21-56`）。
- 会话持久化：`sessions`（含 `channel_type`/`channel_subject_id`）+ `threads` + `thread_messages`
  （`infra/db/repos/sessions.py:59-72`）；thread key 含 `channel_type`（`infra/gateway/threads.py:36-45`）。
- **主动推送四条链路**：cron（`infra/cron/delivery.py:130`）、external events outbox
  （`infra/events/expert_target.py:102`）、主动关怀（`infra/proactive/service.py:252`）、
  团队通知（`infra/agents/teams/team_manager.py:1474`）→ 统一出口 `infra/gateway/gateway.py:824`。
- **HITL 成熟**：`hitl_pending` 表，状态 `pending/approved/rejected/expired`（`infra/gateway/hitl/store.py:16-24`），
  kind = `approval` / `ask`，TTL 30 分钟，IM 内用 `/approve` `/reject` `/pending` 斜杠命令
  （`infra/gateway/slash/catalog.py:18`）；cron 遇 HITL 会挂起而非失败（`infra/cron/delivery.py:243-270`）。
- 通道富文本能力矩阵（`docs/miniapp-im-optimization.md:113-121`）：飞书 interactive card / 企微 Markdown /
  微信纯文本+图 / QQ 纯文本。

**不足**

1. **微信公众号没有通道**。catalog 30 个连接器与 gateway 通道里都没有公众号回调端点，`api/routers/` 下无
   IM callback 入口。PDF 把公众号列为四个合规平台之一 —— 这是 0.1 的唯一硬缺口。
2. **没有外部联系人 / 身份归并**。IM 消息一律映射到 agent 属主，源码注释写明
   "IM members of one agent share the owner's profile, they are not isolated from each other"
   （`infra/gateway/process/message_keys.py:86-108`）。企微里的张三和抖音里的张三是两个 session、
   两个 thread、**同一个 users.id**。PDF 0.1 的核心命题「十年后还在用同一个入口」在 Octop 里没有对应物。
3. **没有"转人工接管"**。`handoff` 只存在于浏览器控制权（`api/routers/browser/harness.py:298`），
   与 IM 无关；没有「AI 挂起 → 通知人工 → 人工接管回复」的状态机（PDF 说的"断点提醒人接手"）。
4. **无平台历史消息回溯**：`octop_gateway/channels/` 全目录无 fetch_history。
5. **主动推送有前置条件**：必须先有 session（对方发过消息），否则推不到。
6. 企微缺「客户联系」与「企微客服」两个子通道（`docs/architecture-overview.md:401-402`）。

**如何补**

| 补什么 | 怎么做 | 落点 |
|---|---|---|
| 公众号通道 | 新建 Official Account 通道（token 校验 + 签名校验回调 + 客服消息接口） | `octop_gateway/channels/wechat_mp.py` + `api/routers/` 回调入口 |
| 外部联系人层 | 新增 `contacts` 表（平台 + 平台内 subject_id + 归并键 + 客户档案指针），thread/session 挂 contact_id | `infra/db/repos/contacts.py` + 新迁移 |
| 身份归并规则 | 以手机号/unionid/自定义归并键做人工确认式合并（不要自动猜） | contacts 合并接口 + dashboard 确认页 |
| 转人工接管 | HITL 扩一个 `handoff` kind：AI 声明交还 → 通知指定人 → 该会话后续消息走人工 | `infra/gateway/hitl/` 扩状态机 |
| IM 富卡片 | 飞书已有；企微/微信先做「Markdown + 商品图 + 链接」降级，够用 | 通道层已有 `app_share_link` |

---

### 0.2 平台监测 — ❌ 不满足（建议降级，不建议做成平台能力）

**Octop 现状**：30 个内置连接器（`infra/connectors/catalog.json`）里**没有**抖音、公众号、
视频号、小红书的数据连接器；`infra/events/connector_poll.py` 只能轮询已有连接器。
理论上可用 `octop-browser`（CDP 驱动 Chrome）打开自有号后台做 RPA 抓取 —— 但这正是 PDF
2.2 判定的"高风险、要做爬虫法律风险"路径。

**不足**：整模块不存在。数据无处可去、无处可存、无处可看。

**如何补**：**建议不做成平台模块**。PDF 自己也把它定成"客户级数据定制（公众号号自研 + 抖音采购 +
小红书不硬碰），自研投入控制在 2 个月内"。落到 Octop 上，正确形态是：

1. 为单个客户写死一个采集 MCP（账号授权 + 轮询），进 `connectors` 目录当客户实例；
2. 数据落到该客户专属的 light_app 表（`light_app_sources` + `light_app_rows`）；
3. 在 dashboard 里用 MiniApp 看板呈现"评论里问价格的人 → 销售线索"。

即：**0.2 降级为 3B 的一个数据源**，而不是一个平台子系统。

---

### 0.3 外部搜索 — ✅ 满足

**Octop 现状**（代码为准，文档里查不到）：

- harness 内建 `web_search` 工具族：`tavily.py` / `kimi.py` / `_registry.py`
  （`octop_harness/builtin/tools/web_search/`），注册为 5 个工具
  （`infra/agents/settings/tool_catalog.py:72-76`：tavily/brave/google/kimi/searchfree）。
- `web_fetch` 工具：抓 URL 转 markdown，带 SSRF 防护与 5MiB 上限
  （`octop_harness/builtin/tools/web_fetch.py:1-30`）。
- 设置页可探活：`POST /api/search/{provider_id}/test`（`api/routers/search.py:26`），
  provider 集合 `tavily/brave/google/kimi`（`infra/utils/search_probe.py:20`）。

**不足**：几乎无。只需两件小事：
1. 提示纪律「搜不到就说没找到，严禁自行补全」+ 每条结论附来源站点 —— 写进 Agent 系统提示 / SKILL.md；
2. 少量「时效性/长尾事实」确实答不上时，属模型能力边界，不是平台缺口。

**如何补**：配两个 provider（对应 PDF 的"博查 + 智谱"）即可开工。

---

### 1.1 ERP 只读适配器 — ❌ 不满足（但载体已现成）

**Octop 现状**

- **零业务表**。53 个 repo（`infra/db/repos/`）全是平台表：agents / channels / threads / cron /
  connectors / knowledge / light_app_* / teams / goals / external_* / trajectory …
  **没有订单、库存、客户、商品任何一张表**（已用 `CREATE TABLE` 全量扫描确认）。
- **无数据库类连接器**：30 个 catalog 条目里无 MySQL/PostgreSQL/SQL Server，也无 Airtable/Sheets/CRM。
  venv 里只有 `psycopg`，**没有 pyodbc/pymssql/mysql**。
- ✅ 凭据加密有：`infra/connectors/crypto.py:14`（Fernet，密钥进 `secrets` 表）。
- ✅ 自定义 MCP 有：`infra/connectors/custom_mcp.py:17`，支持 `streamable_http` 与 `stdio`。
- ✅ **中间层载体已现成**：`light_app_sources` 表带 `mode(append|snapshot)`、`schedule_seconds`、
  `mapping_json`、`upsert_key`（`infra/db/migrations/025_light_apps.sql:68-79`），
  行落 `light_app_rows` —— 这正是"定时拉取 + 字段映射 + 幂等去重 + 落表"四件套。
- ⚠️ **但数据源只吃进程内 gateway 连接器**：`infra/apps/sources.py:1-7` 明写
  "Only in-process gateway connectors (mcp_mode=gateway) are supported … HTTP MCP servers would
  need a client stack in Octop — out of scope for now." → 自定义远程 MCP **不能**当 light_app 数据源。
- ⚠️ `http-extract` 只支持公共只读、无鉴权的 GET（`infra/connectors/gateway/adapters/http_extract.py:21-47`）。
- ✅ 只读强制有一处：`infra/apps/sources.py:91` `_require_read_only` 按 MCP `readOnlyHint` 拒绝写工具。
- ❌ 无增量水位（CDC）、无表结构识别、无字段语义映射、无清洗规则。

**不足**：PDF 判定这个模块"前两段是人工（表结构识别 ~40%、字段语义映射 ~30%），不是技术密集型，
是耐心密集型"。Octop 的技术缺口其实只有一个小点（SQL Server 适配器），**真正的活是每家客户的映射**。

**如何补（最小可行路径）**

1. **新增 SQL Server gateway adapter**（进程内 Python adapter，走 `pymssql`），进 `catalog.json` 并标
   `readOnlyHint=true` → 立刻能被 `light_app_sources` 当数据源（这一步解开 `sources.py:1-7` 的限制）。
2. **每家客户做一份 mapping**（表名 → 列名 → 语义），AI 生成初稿 + 人工确认，
   存进 `light_app_sources.mapping_json`。
3. **四张标准表建成 4 个 light_app 表**（订单/库存/客户/商品），`mode=append` + `upsert_key` 幂等，
   `schedule_seconds` 设为夜间频率。
4. **只读双保险**：数据库侧只读账号 + `DENY INSERT/UPDATE/DELETE`（PDF 2.4.2 的 SOP）；
   应用侧 `_require_read_only` 兜底。
5. **兜底路径**（客户只有 Excel）：走 workspace 落盘 + miniapp 导入 / `light_app_sources` 的
   `workspace_file` 源（`infra/apps/service.py:405-407`）。
6. CDC/增量同步先不做：`upsert_key` + 定时全量 append 已能覆盖 PDF 说的"每晚更新一次"。

---

### 1.2 屏幕与会议实时读取 — ⚠️ 零件齐全，缺最后一段装配

**Octop 现状**

- ✅ **截图能真正进模型**：`browser_use` 截图返回 `[image_block, text_block]` 多模态块
  （`octop_harness/builtin/tools/browser_use.py:126-174`，`media_type: image/png`），
  注册于 `agent.py:1367` / `tool_catalog.py:66`。
- ✅ **桌面截图工具**：`desktop_screenshot` 抓 X11（mss / ImageMagick），
  PNG 落在 agent workspace `outbound/screenshots/`（`octop_harness/builtin/tools/desktop_screenshot.py:288-345`）。
- ✅ **实时 ASR**：腾讯云实时 STT（`infra/voice/realtime.py`），
  `WS /api/voice/stt-stream`（`api/routers/voice.py:256`）；浏览器麦克风采集
  （`dashboard/src/hooks/useVoiceInput.ts:323` getUserMedia / `:479` MediaRecorder）。
- ✅ **推流**：浏览器 4fps JPEG WS（`api/routers/browser/stream.py:335`）、
  远桌面 WS（JPEG + 键鼠注入，`api/routers/desktop/stream.py:235`）。
- ✅ **OCR 引擎可用**：`rapidocr` 额外依赖（`pyproject.toml:75-78`）；`ocr-and-documents` 技能
  提供 PDF/扫描件抽取（pymupdf / marker-pdf）。
- ✅ **腾讯会议官方 MCP**（`infra/connectors/catalog.json:60-79`，
  `mcp.meeting.tencent.com/mcp/wemeet-open/v1`，`infra/connectors/builder.py:179`）。
- ✅ **可注入进行中的一轮**：`steer` 支持 image/file 块（`infra/gateway/steer/coordinator.py:666-687`），
  且块格式是 `workspace_path` 引用 —— 与 `desktop_screenshot` 的落盘路径正好对上。
- ❌ **截的是服务器虚拟桌面 / 受控浏览器，不是用户本机屏幕**；无本机上行通道。
- ❌ **无"持续感知 → 自动触发推理"的回路**。事件平面是摄入侧（webhook / 300s 轮询，
  `infra/events/worker.py:677-681`），截屏流只到 dashboard，不回流 Agent。
- ❌ **无无障碍树**（只有 CDP `DOM.getDocument` 的 DOM 文本 → canvas 应用读不到字），
  需 OCR 补。
- ❌ 飞书会议 / Zoom 无接入；腾讯会议以会后录制/纪要为主，非逐句实时。
- ❌ 无桌面悬浮窗 UI；桌面壳无麦克风采集。

**不足**：PDF 的关键判断是"先读录音转文字，再实时截屏；**当'做'优先 —— 先看画面**，树里没有目标文本
不裁剪"。Octop 的做法是反的默认：截图是工具调用触发的（人点一下），不是持续采样。

**如何补**

1. **原型（最短路径，零件全在）**：
   桌面壳加「截屏 + 麦克风上行」→ 帧写入 agent workspace `outbound/screenshots/` →
   `steer` 以 image 块注入进行中的一轮 → 模型结合 ERP 历史（1.1）与转写文本给建议。
   缺的只有"帧怎么上来"和"什么时候注入"。
2. **产品化**：
   - 加一个采样循环（屏幕帧 + 转写片段），做节流与去重；
   - 注入策略改为**新轮次**（而不是 steer 打断），并保留 steer 作为"老板插话"通道；
   - canvas 场景补 rapidocr 识别；
   - 单进程 `ws_hub` 补跨机扇出（目前无 Redis pub/sub）。

---

### 1.3 客户分层与档案 — ❌ 不满足

**Octop 现状**

- ❌ 无客户表、无标签表、无分层模型（全仓无 customer/contact/lead/segment/RFM 业务表）。
- ❌ `knowledge_bases` / `knowledge_documents` 是**文档型 RAG**：每 KB 一个 SQLite sidecar 存向量，
  查询全表余弦扫描，无全文/BM25、无字段过滤（`infra/knowledge/index.py:25-35`）；
  k=8、上下文预算 6000 字（`infra/knowledge/retrieve.py:17-24`）。**不能当客户档案用**。
- ⚠️ CSV/XLSX 进知识库会被**拍平成 tab 分隔文本再切块**（`infra/knowledge/parse.py:45,74,262`）
  → 列语义丢失、不能按价格/库存过滤、无实时性。商品表绝不能这么进。
- ⚠️ **IM 身份塌缩到属主**（`infra/gateway/process/message_keys.py:86-108`）
  → "沟通记录"无法按客户归集，这是 1.3 的硬阻碍。
- ✅ 载体可用：light_app 自定义表 + 行 + PII 机制（`infra/apps/pii.py`）。

**不足**：PDF 指出的真难点是"同一客户有三套价格，AI 要能拿库存生产年份分析" ——
即**累计成交价随时间变**。Octop 连客户实体都没有，这层自然无从谈起。

**如何补**

1. **依赖 0.1 的 contacts 层**（外部联系人 + 身份归并）—— 否则沟通记录无法归集客户。**依赖关系明确，先做 0.1。**
2. 客户档案落 light_app 表：`customers`（客户 id、累计成交价、到店次数、最近到店时间、首单时间）
   + `orders` 明细表。
3. **累计成交价不要预计算成字段**：每次现算（订单表按客户聚合），
   避免"同一客户多套价格"的历史漂移 —— 这正是 PDF 点名的坑。
4. **分层不要做规则引擎**：PDF 说"不用老模板，系统自己算"。让 Agent 在夜间跑批里读表 + 算
   铁杆/潜力/沉睡/走 + 写回 tag 列（`app_update_row` 已有）。符合"最小实现"原则。
5. 沟通记录侧：contacts ↔ sessions 关联，让 Agent 能读"这个客户上次说什么"。

---

### 2A 经营诊断与归因 — ⚠️ 跑批链路有，指标与归因没有

**Octop 现状**

- ✅ **夜间跑批机制齐全**：cron 触发四类 —— `cron:` / `interval:` / `date:` / `agently:`
  （`infra/cron/trigger.py:77`）；`task_type` = `text`（直推）/ `agent`（跑完整 ReAct 回合，
  `infra/cron/task_type.py`）；cron job 可挂 MCP servers（`infra/db/repos/cron.py:92`）。
- ✅ **结果推 IM**：`infra/cron/delivery.py:71` → `gateway.push_session_text` → 通道发送。
- ✅ **数字可信机制**：Light App 服务端聚合 + 声明式图表 spec（`infra/apps/charts.py:20`，
  6 种图表 + sum/avg/count/min/max），只读助手防模型编数（`infra/apps/assistant.py`）。
- ✅ **决策链可回溯**：`trajectory_events` append-only 事件流（`infra/history/trajectory/`，
  REST `api/routers/chat/trajectory.py:239-380`）。
- ❌ **无指标层**：毛利、进货价、周转天数这些口径没有定义处。
- ❌ **无归因算法**：价 vs 量 的分解只能靠 LLM 即席生成，无确定性算法层。
- ❌ **无诊断产物表**：cron 只留 `last_run_at/last_status/last_error`
  （`infra/db/repos/cron.py:94-96`），产物落在 `thread_messages` 里
  → "昨晚 03:12 产出的那份诊断"**无处可查**。
- ❌ 无异常阈值与告警规则。
- ❌ 无 pandas / matplotlib；venv 只有 numpy → 计算需在 `execute` 沙箱里自行装依赖。
- ❌ 跨天跨源归因无关联边（trajectory 是单会话流）。

**不足**：PDF 说 2A 的价值是"输出一个动作，不是张报表"、"等营业额掉了才开始问永远慢一步"。
Octop 能跑批、能推结果，但推出来的结论**没有落点、没有口径、不可复查**。

**如何补**

1. **指标层**：把毛利 / 进货价 / 周转定义成显式配置（指标名 + 计算式/映射 + 口径说明 + 适用门店），
   存 light_app 表或 `settings`，**不要硬编码进 prompt**。这是 2A 与 3B 的共同前置。
2. **诊断产物表**：新建 `diagnostics`（run_at, agent_id, job_id, scope, metric, conclusion,
   evidence_json, confidence, source_note）。同时解决 2A"无处可查"与 3B"底注要写昨晚 03:12 同步"。
3. **归因**：不必写算法库 —— 让 Agent 在跑批里做价/量分解并把证据链写进 `evidence_json`；
   若要确定性，用 light_app 的服务端聚合做对照，LLM 只负责叙述。
4. **异常检测**：先做「环比波动超阈值 → 标注」，阈值入配置。
5. **依赖**：指标层依赖 1.1 的四表落地，否则毛利算不出来。

---

### 2B 实时参谋 — ⚠️ 传输层齐，触发回路缺

**Octop 现状**

- ✅ chat WS 逐 token 推送 + **turn 内 steer**（`api/routers/chat/ws.py:280-292`）；
  steer 支持 image/file 块（`infra/gateway/steer/coordinator.py:666-687`）。
- ✅ HITL 审批、WS hub 扇出（`infra/gateway/ws/ws_hub.py:129`）。
- ✅ 实时 ASR（腾讯）+ 截图可进模型（见 1.2）。
- ❌ **无秒级自主建议回路**：没有任何组件持续监听屏幕/音频并自动触发一轮推理；
  事件平面是摄入侧 + 300s 轮询（`infra/events/worker.py:677-681`）。
- ❌ steer 的块是 `workspace_path` 引用而非原始图片 → 帧必须先落 workspace（可行，见 1.2）。
- ❌ 单进程 `ws_hub`，无跨机 pub/sub。
- ❌ **无客户历史成交价/账期数据源**（依赖 1.1、1.3）—— PDF 明确说这是 2B 的前提。
- ❌ 无本地指标缓存 → 每次对标都要查 ERP，达不到 ≤2-3 秒。

**不足**：PDF 的判词很准 —— "国内把'实时'做成了'实时看板/实时预警'，那是给老板看的监控，
不是给销售用的提示"。Octop 目前连看板那种都没有，更没有提示。
但**它有 PDF 没提到的优势**：steer 机制天然支持"在对话进行中把新观测喂进去"。

**如何补（按 PDF 建议的优先级）**

1. **先做「会前准备」**（低延迟、价值立刻可见）：
   cron `date:` 一次性触发 → 生成一页纸（历史成交价 / 账期 / 最近投诉 / 预判异议）→ 推 IM 或落 workspace。
   这条链路 Octop 现有能力**全部具备**，缺的只是客户档案数据（1.1/1.3）与模板。
2. **再做「会中提示」**（高难度，需三件同时到位）：
   - ① 屏幕帧 + 转写片段的持续采集（1.2）；
   - ② 指标/历史数据的**本地毫秒级缓存**（不能每轮查 ERP）；
   - ③ 一个低延迟触发器，把新观测作为新轮次输入，同时保留 steer 插话通道。
3. **延迟预算**：PDF 说 1-2 秒对结论完全够用，不需要客服级的 500ms ——
   这意味着**不必追求流式**，一个 1-2 秒一帧的采样 + 小模型判据就够，别过度设计。

---

### 3A 销售支持 — ⚠️ 知识形态错位

**Octop 现状**

- ✅ 话术/规则可承载：`SKILL.md`（frontmatter name/description，按描述渐进触发，
  `infra/agents/experts/library/business-operations/skills/bo-runbook/SKILL.md:1-3`）、
  技能包与 SkillHub（`infra/skills/`、`infra/db/repos/skill_packages.py:46`）。
- ✅ 维护界面现成：知识库页（`dashboard/src/pages/KnowledgeBases/`）、
  技能包页（`dashboard/src/pages/SkillPackages/`）。
- ⚠️ **结构化商品表进 RAG 会坏**：CSV/XLSX 被拍平切块（`infra/knowledge/parse.py:45,74,262`）
  → 无列语义、无法按价格/库存过滤、无实时性。
- ✅ 活数据有正路：light_app 数据源（只读 gateway 连接器 + 定时拉取 + 落 `light_app_rows`）
  + 只读助手 + `app_read_data` 等 23 个 app 工具（`infra/apps/tools.py:28`）。
- ⚠️ **IM 内交付形态受限**：微信仅文本/图/文件/视频、无卡片；企微文本 + Markdown；
  只有飞书支持 interactive card（`docs/miniapp-im-optimization.md:113-121`）；
  IM 内 `app_open` 只给公开链接 + 预览图（`infra/apps/tools.py:600-630`）。
- ❌ 无"按场景检索话术"的规则检索器（只有向量检索 + 技能描述匹配）。
- ❌ **KB 可见性只有 owner 或全局 `shared=1`，无按角色/门店 ACL**
  （`infra/db/repos/knowledge.py:178`）→ 做不到"店长维护本店话术、店员只见本店"。
- ❌ **无 per-IM-user 身份与画像** → "首次对话就知道这是新人"做不到（同 0.1）。
- ✅ `users` 表有 role / role_name / display_name / locale / preferences_json
  （`infra/db/migrations/001_initial.sql:16-28`），可承载"店员/店长"角色，但 IM 侧映射不到具体人。

**如何补**

1. **商品/价格/库存 → 进 light_app 表，不进知识库**；异议话术、常见案例 → 进知识库（文档形态正确）。
   检索时 Agent 先查结构化表（`app_read_data`）再查话术库。
2. **IM 交付**：企微/微信用「Markdown + 一张商品图 + 链接」——现有能力够用；
   要真卡片与按钮，需飞书或另做企微侧。
3. **新人识别**：依赖 0.1 的 contacts 层（按 sender 计数 / 首触标记），不要指望 `users` 表。
4. **店长维护**：需要补 **KB 按角色/门店的 ACL**（当前只有 owner/shared 二选一），
   否则"店长维护话术"没有权限落点。
5. 四类异议话术（太贵 / 想再看看 / 别家更便宜 / 效果不好）做成 4 个 SKILL.md 或一个带分类的文档集，
   人工维护 —— 与 PDF 的"话术库由谁维护"结论一致。

---

### 3B 经营驾驶舱 — ⚠️ 形态有，语义空

**Octop 现状**

- ❌ **没有简报/驾驶舱页面**。`dashboard/src/pages/` 只有 Admin / Agent / Apps / Chat / Control /
  Experts / Invite / KnowledgeBases / Login / PwaDebug / Settings / Setup / SkillPackages / Teams；
  路由里最接近的是 `/apps/boards/:boardId`（MiniApp 看板）。
- ✅ MiniApp 能做卡片式看板：绑定数据源 `light_app_services`
  （`allowed_tools` + `allowed_url_prefixes` + `cache_ttl_seconds`，
  `infra/db/repos/app_services.py:31-46`）、行级动作 `app_update_row` / `app_append_row`、
  图表 6 种、SDK 11 能力位（`dashboard/src/components/MiniAppFrame/sdk.ts:139`、
  `capabilities.ts:10-22`：`listRows/appendRow/updateRow/onDataChange/generate/launchGame/
  composeMessage/service().call`）。
- ⚠️ 页面只能 `appendRow`，不能改行；过滤排序**完全未下推**（16ms/千行、2 万行 323ms）。
- ❌ 无「结论—数字—异常—待办」四段式模板。
- ❌ 无"数据来源 · 同步时间"字段（`light_app_services` 无 last-synced / fetched_at）。
- ❌ 无 cron 产物表 → 昨晚那份诊断无处可查（同 2A）。
- ❌ **4 个 KPI 零埋点**：`infra/metrics.py` 是进程内存计数器（无 user_id、无持久化、无时间序列）；
  `light_app_events` 是外部事件收件箱不是行为埋点；`notifications` 的 read/unread 只能近似。
- ❌ 无结构化动作协议（只有通用 app 工具）；"一键打电话"无对应能力。
- ⚠️ app 级 SSE 推送待做（`docs/miniapp-product-plan.md:383`）→ 简报推不出去。

**如何补（顺序不能颠倒）**

1. **cron 产物表**（与 2A 共用）：`diagnostics` 表 → 存 run_at / summary / payload / source_note。
   这是"昨晚 03:12 同步"的实现前提。
2. **简报页**：`/briefing`（或做成一个 MiniApp + board）：每天读最近一次产物，
   渲染「结论—数字—异常—待办」四段，底部写来源脚注（数据源 + 同步时间 + 只读声明）。
3. **结构化动作**：待办行带 `action_type`（改价 / 生成话术 / 打电话），点击 → 调
   `app_update_row` / `generate` / 拨号链接。这是 3B 区别于报表的关键。
4. **埋点**：新建 `briefing_events`（user_id, event_type: `open`/`click`/`adopt`, ts）。
   三个事件覆盖 PDF 的日活打开率、待办点击率、建议采纳率；次月留存用 `usage_log` 近似。
5. **推送**：先用 IM 纯文本简报（cron 已能推，`infra/cron/delivery.py:130`），
   app 级 SSE 后置。
6. **不做 PC 大屏**（PDF 明确不做），也不做自定义报表/多维分析 —— 与 MiniApp 定位一致。

---

### 3C 营销短视频 — ⚠️ 生成有，链路断

**Octop 现状**

- ✅ **文生视频 / 图生视频 / 图**：`generate_video` / `generate_image`
  （`infra/agents/settings/tool_catalog.py:77-78`），多 provider
  （volcengine / dashscope / minimax，默认 doubao-seedance-2-0-mini、seedream-5-0-lite，
  `infra/agents/settings/media_generation.py:22-28,64`）。
- ✅ TTS / STT：edge-tts / 腾讯 / OpenAI / mimo（`infra/voice/adapters.py:252-265`）。
- ✅ 文档 / PPT / XLSX 生成：python-docx / python-pptx / openpyxl（`pyproject.toml:43-46`）
  + office-automation 专家技能 → 能出"文档小样"。
- ❌ **无 ffmpeg 依赖** → 无剪辑、无模板拼装、无字幕、无数字人、无换脸。
- ❌ 无素材库 / 行业模板机制（素材只能落在 workspace 靠 agent 手工组织）。
- ❌ 无批量分发框架；只有微信公众号草稿 / 小红书脚本
  （`infra/agents/experts/library/wechat-ops/skills/publisher-multi-platform/scripts/wechat_publish.py`）。
- ⚠️ **归因底座可用但无领域模型**：`external_events`（webhook inbox，去重/重试/outbox，
  `infra/db/repos/external_events.py:61`）+ `light_app_events` 收件箱
  （`infra/db/repos/app_events.py:51`）。
- ✅ 仓库自身结论与 PDF 一致：视频→订单精确归因不可得，只能用优惠码 / 专属 SKU / 时间窗代理
  （`docs/ai-video-commerce-system.md` §1.3）。

**不足**：PDF 的判断是"生成能力已经极到位，打不打过视频；但没有一家回答'这条视频带来几个到店客人'"。
Octop 同样停在生成侧。

**如何补**

1. **模板层**：把"行业模板"做成 SKILL.md（模板结构 + 变量约定 + ffmpeg 命令规范）；
   素材走 workspace（已有产物白名单机制 `dashboard/src/pages/Apps/desktopFiles.ts:20-27`）。
2. **加 ffmpeg 依赖 + 一个剪辑工具**（模板变量替换 + 字幕 + 竖屏裁切 + 拼接 BGM）。
   这是一处确定要做的工程活。
3. **分发**：只接已有能力（公众号草稿），其余平台人工导出。**不要追平台官方发布接口**
   （PDF 判定抖音/小红书批量分发合规受限）。
4. **归因（值得做，PDF 说"没人做的那第三件"）**：
   建 `attribution` 表（code, video_id, customer_id, touched_at, converted_at）；
   优惠码在 IM / miniapp 入口领用 → `external_events` webhook 回写 → 与订单表 join。
   这条链路 Octop 的事件平面天然能承载，是**平台能直接吃下的差异化点**。
5. **合规**：AIGC 标识、肖像权 —— 不做换脸是对的（PDF 也判"不按正路"）。

---

## 4. 横向结论

**Octop 的能力分布**

| 层 | 状态 | 说明 |
|---|---|---|
| 0 连接 | **强（但缺一层）** | 10 通道收发、群聊语义、HITL、四条主动推送链路都在；缺公众号、缺跨平台客户档案、缺转人工 |
| 1 感知 | **一半是形态，一半是数据结构** | 截屏/ASR/推流/OCR/腾讯会议 全部现成，缺"用户本机上行 + 持续触发"；ERP 四表与客户档案为零 |
| 2 洞察 | **空** | 能取数、能跑批、能推结果，但没有指标层、没有归因算法、没有实时回路 |
| 3 输出 | **有形无神** | MiniApp 卡片+数据绑定+动作回调、IM 文本/图/链接、文生视频都齐；但"待办是什么、点按钮做什么、数据源是谁"全是空的 |

**定位判断**：Octop 当前定位是「单节点多 Agent 平台」
（`docs/architecture-overview.md:27`），路线图目标是「单节点、自托管、面向具体业务的 AgentOS」
（`docs/agent-os-roadmap.md:6`）。这十模块暴露的正是那"一层业务"：
**Octop 缺的不是 Agent 能力，是零售/门店的业务契约**（标准业务表 + 指标层 + 客户档案 + 归因口径 + 归因埋点）。

**能直接吃下的 vs 必须自建的**

| 可直接吃下（配置即可） | 需要补一段 | 必须自建 |
|---|---|---|
| 0.3 外部搜索（provider 已通） | 0.1 公众号通道 | 1.1 ERP 四表 + 映射（人工为主） |
| 0.1 企微/飞书/钉钉/HITL/主动推送 | 1.2 本机上行 + 持续触发 | 1.3 客户档案 + 分层 |
| 3A 话术（SKILL + KB） | 2A 指标层 + 诊断产物表 | 2A 归因口径与算法层 |
| 3B MiniApp 看板形态（图表 6 种、动作回调） | 3B 简报页 + 四段式 + 埋点 | 3B 结构化动作协议 |
| 3C 文生视频 / 图 / 文档 | 3C ffmpeg 剪辑 + 模板 | 3C 到店归因领域模型 |

---

## 5. 建议路线（按依赖顺序，不按热闹程度）

**阶段 0 · 地基（无依赖，先做，1-2 周）**
1. `contacts` 外部联系人表 + 跨平台身份归并（解 0.1 / 1.3 / 3A）
2. `diagnostics` 诊断产物表（解 2A / 3B 的共同前置）
3. SQL Server **gateway adapter**（只读，`readOnlyHint=true`）（解 1.1 的唯一技术缺口）

**阶段 1 · 数据层（依赖阶段 0，2-4 周）**
4. 四张标准表落 light_app（订单/库存/客户/商品）；**一家客户跑通 = 一套模板**
5. 指标层定义（毛利/进货价/周转）+ 归因提示规范
6. 客户档案 + Agent 现算分层（不写规则引擎）

**阶段 2 · 输出层（依赖阶段 1，2 周）**
7. 简报页 + 四段式模板 + 来源脚注 + 3 个埋点事件（open/click/adopt）
8. 待办结构化动作（改价 / 生成话术 / 打电话）
9. KB 按角色/门店 ACL（店长维护话术的权限落点）

**阶段 3 · 实时层（依赖阶段 1，风险最高，3-4 周）**
10. 桌面壳屏幕 + 麦克风上行 → steer/新轮次注入 → 会中建议
11. 指标本地毫秒级缓存（对标不能每轮查 ERP）

**建议明确放弃 / 降级的**

- **0.2 平台监测** → 降级为客户级数据定制（不进平台）
- **3C 的一键成片、换脸、批量分发** → PDF 已判定不做，Octop 同
- **2B 的"会中提示"若排期紧** → 退回"会前准备"（cron `date:` 一行就能起）
- **多租户化** → 沿用现行"每客户/每门店一个实例"（`docs/agent-os-roadmap.md:32`），
  不要为这十个模块引入 tenant 维度

---

## 6. 与 PDF 的分歧点（Octop 不必跟随 PDF 的取舍）

| PDF 结论 | Octop 的情况 | 建议 |
|---|---|---|
| 0.1 只做四平台，放弃抖音/小红书 | Octop 也无这些通道 | **一致**，不必追 |
| 0.3 接博查 + 智谱两家 | Octop 已通 tavily/brave/google/kimi/searchfree | **比 PDF 宽**，两家即可起步 |
| 1.1 不自建 ERP | 一致 | 但要自建**标准表中间层**（PDF 没提这层） |
| 1.2 用桌面悬浮窗 + 麦克风 | Octop 截的是服务器桌面 | **形态要做，但注意服务器侧与本机侧的区别** |
| 2B 国内"事中实时"是空白 | Octop 有 steer 可注入 | **steer 是 Octop 的独有优势**，但缺触发器与数据缓存 |
| 3B 只考核四个 KPI | Octop 四个 KPI 全无埋点 | 埋点是必做，且很轻（一张事件表） |
| 3C 归因"没人做过" | 事件平面已具备 | **这是 Octop 最有机会做出差异化的单点** |

---

## 附：主要证据索引

| 主题 | 位置 |
|---|---|
| 通道实现（外部包） | `.venv/lib/python3.12/site-packages/octop_gateway/channels/`、`constraints.py:29-97`、`group_context.py:21-56` |
| IM 身份归并缺失 | `src/octop/infra/gateway/process/message_keys.py:86-108` |
| HITL | `src/octop/infra/gateway/hitl/coordinator.py`、`store.py:16-24`、`slash/catalog.py:18` |
| 主动推送四链路 | `src/octop/infra/gateway/gateway.py:824`、`cron/delivery.py:130`、`events/expert_target.py:102`、`proactive/service.py:252` |
| 搜索工具 | `src/octop/infra/agents/settings/tool_catalog.py:72-76`、`infra/utils/search_probe.py:20` |
| 截图进模型 | `octop_harness/builtin/tools/browser_use.py:126-174`、`desktop_screenshot.py:288-345` |
| steer 注入 | `src/octop/infra/gateway/steer/coordinator.py:666-687` |
| 实时 ASR | `src/octop/infra/voice/realtime.py`、`api/routers/voice.py:256` |
| cron 能力 | `src/octop/infra/cron/trigger.py:77`、`task_type.py`、`infra/db/repos/cron.py:79-98` |
| 连接器目录（30） | `src/octop/infra/connectors/catalog.json`、`gateway/adapters/http_extract.py:21-47` |
| light_app 中间层 | `src/octop/infra/db/migrations/025_light_apps.sql:68-79`、`infra/apps/sources.py:1-7`、`service.py:405-407` |
| 图表与只读助手 | `src/octop/infra/apps/charts.py:20`、`assistant.py` |
| 知识库为文档 RAG | `src/octop/infra/knowledge/index.py:25-35`、`parse.py:45,74,262`、`retrieve.py:17-24` |
| 媒体生成 | `src/octop/infra/agents/settings/media_generation.py:22-28,64` |
| 平台定位 | `docs/architecture-overview.md:27`、`docs/agent-os-roadmap.md:6,32` |
