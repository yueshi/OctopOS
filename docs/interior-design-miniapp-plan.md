# 对话式装修设计 MiniApp — 架构与落地计划

> 分支：`feature/interior-design-miniapp`　基线：`feature/miniapp-ai-native` @ `52800c44`（schema v52）
> 状态：**设计已定，模板侧已起板**。几何内核独立成微服务，本仓库只保留「专家（对话面）+ MiniApp（复核面）」。

---

## 0. 结论

对话式装修设计的**确定性内核不能放进 MiniApp**。MiniApp 页面只在浏览器沙箱里跑 JS，没有服务端执行、没有网络（`docs/miniapp-overview.md:68`、`src/octop/infra/apps/sandbox.py:31`），因此它只能承载**展示与人工复核**。

最终形态是三件协同的东西：

```
┌─ Octop（本仓库）───────────────────────────────────────────┐
│ ① 装修设计专家（SOUL/AGENTS/skills，纯 markdown）             │
│    对话面：澄清意图 → 改 IR → 调校验 → 读诊断 → 修复          │
│      │ 读写 IR（app_* 工具）        │ 调内核（MCP 工具）        │
│      ▼                             ▼                         │
│ ② MiniApp「装修方案」（模板 interior-plan）                 │
│    平面图 SVG + 诊断清单 + 工程量表 + 房间面积                  │
│      │ 读 IR 表 / 写诊断表      │ 调内核（extension service）  │
└──────┼───────────────────────────┼─────────────────────────┘
       │                           │
       │  custom-mcp connector     │  provider=extension service
       │  （回环/局域网免 SSRF）    │  （需 admin 开 allow_private_network）
       ▼                           ▼
③ 几何内核微服务（独立进程，代码在 src/octop/infra/design/）
   MCP: check_drawing / takeoff / apply_ops
   REST: POST /check_drawing、POST /takeoff、POST /ops、GET /health
   纯函数：输入 IR JSON → 输出诊断 + 工程量
```

**IR（结构化设计状态）存在 Octop 侧的 MiniApp 表里**，微服务无状态。这样 IR 在 Octop 里可见、可审计、可导出、可被 Agent 工具读写；微服务只负责"算"。

### 当前进度

| 层 | 状态 |
|---|---|
| ② MiniApp 模板 `interior-plan` | ✅ 页面、schema、封面完成（`src/octop/infra/apps/system_templates/interior-plan/`） |
| ① 装修设计专家 | ✅ manifest + SOUL + AGENTS + 2 个 skill（`src/octop/infra/agents/experts/library/interior-design/`） |
| ③ 几何内核 | ✅ IR / 校验 / 算量 / 操作 / REST / MCP 已实现，25 个用例（`src/octop/infra/design/`） |
| 端到端联通 | ✅ 三条链路已在集成测试中真跑通，见下方「端到端验证」 |
| 真实部署 | ✅ 在全新 `OCTOP_HOME` 上起真实实例验证通过，见「真机验证」 |

### 端到端验证（已跑通）

`tests/integration/test_interior_design_flow.py` 与 `tests/integration/test_interior_plan_page.py` 用真实服务验证：

| 链路 | 怎么验的 |
|---|---|
| 页面 → 内核（extension service） | 起真内核 HTTP 服务 + 真 `AppService`：建模板应用 → 建四张表 → 灌 IR 行 → 配 `provider=extension` 服务 → `invoke_service("check_drawing")` → 诊断写回 `diagnostics` 表 → 经 `/rows` 读回；并断言诊断引用的元素 id 都存在于 `main` 表（否则页面无从高亮） |
| 专家 → 内核（MCP） | 真起 FastMCP `streamable-http`，用 MCP client `initialize` → `list_tools` → `call_tool("check_drawing")`，与 REST 返回同一组 `rule_id` |
| 专家 → 内核（custom-mcp 连接器） | `PUT /api/connectors/custom-mcp` 保存内核地址 → 启动 agent → `prepare_chat_mcp` 返回空失败集 → agent 手上出现 `geometry_check_drawing` / `geometry_takeoff` / `geometry_apply_ops` |
| 页面 IR 契约 | 真 Chromium 加载模板页，桩桥记录页面发出的 IR：断言 `furniture[].kind` 与 `openings[].host` 都带上了、房间多边形正确、诊断写回后 blocking 元素被染成红色 |

**这轮抓到的真 bug**：页面构建 IR 时漏掉了 `furniture[].kind` 和 `openings[].host`，导致衣柜净空、湿区电气这些**依赖 kind 的整族规则从页面路径永远不触发**（专家路径正常，所以单测和 REST 集成测试都发现不了）。已修，并加了 Chromium 回归测试；用"临时回退修复"验证过该测试确实会变红。

### 真机验证（全新实例，2026-11-26）

用 `octop init` 建全新 `OCTOP_HOME`、`octop run` 起真实服务，全程只走 HTTP API（不经过测试夹具）：

| 步骤 | 结果 |
|---|---|
| `POST /api/connectors/custom-mcp/test`（inline spec 指向内核 MCP） | `ok=true`，`tool_count=3`，返回三个工具及描述 |
| `PUT /api/connectors/custom-mcp` 保存为 `geometry` | 200，读回一致 |
| 建 agent 并启动 | 204 → `state=running` |
| 发起一轮聊天（WS `user_turn`，`mcp_servers=["geometry"]`） | 日志：`prepare_chat_mcp ... tool_count=0 missing=['geometry']` → `tool_count=3 missing=[]` → `matching_tools=['geometry_apply_ops','geometry_check_drawing','geometry_takeoff']` |
| `POST /api/apps/from-template`（system 模板） | 201，capabilities 含 `external.write` |
| `PUT /api/apps/{id}/services/cad`（admin 开 `allow_private_network`） | 200 |
| `POST /api/apps/{id}/services/cad/invoke` | 200，`status=succeeded`，返回 4 条诊断（含 `clearance.wardrobe_front` blocking） |

**确认了两个设计假设**：

1. **custom-mcp 连接器是「每用户手动配一次」，模板无法自带**——`services.json` 与模板 manifest 都不支持声明 custom MCP。这是当前路线唯一的强制手工步骤。
2. **custom MCP 工具是「延迟加载」的**：agent 启动时只登记空占位（日志 `defer custom MCP ... load on chat`），真正的工具在首轮聊天前由 `prepare_chat_mcp` 拉取并缓存（按 user+server+spec 指纹）。所以「专家启动后立刻能用内核工具」这个预期是错的——**必须先发一轮消息**，工具才进上下文。专家的 AGENTS.md 已提醒用户首次使用先发一句话。

### 真实户型走查（2026-11-27）

用一套 **81㎡ 使用面积 / 两室两厅一卫** 的户型走完整流程：专家录入 11 面墙、10 个门窗、8 件家具、7 个房间，然后校验 → 修复 → 复检 → 算量 → 页面复核。截图见 [`docs/assets/interior-plan-review.png`](./assets/interior-plan-review.png)。

| 轮次 | 内核返回 |
|---|---|
| 第一轮 | 5 条 blocking：① 卫生间插座落在淋浴轮廓内并处于溅水区；② 主卧窗按 180° 锚错了端点，**超出墙端 1000mm**；③ 门厅没有通向室内的门，主卧/卧室/客厅/厨房/门厅全部不可达；④ 通风（`not_verifiable`，按设计交回专业人员） |
| 修复后全量复检 | 5 → 1：只剩 `ventilation.service_room`（`not_verifiable`）。**其中一条是我修复动作自己引入的错误**（新门 D-006 超出墙端 200mm），被第二轮校验抓住后改正 |
| 算量 | 地面 81.0㎡、墙面（净）166.7㎡、踢脚线 92.4m、门 7 樘、窗 5 樘；房间面积逐间 12.6 / 10.8 / 6.3 / 2.7 / 28.8 / 5.4 / 14.4㎡，合计与总量一致 |

结论：**校验确实能抓到人（和模型）会犯的录入错误，而且修复循环能收敛**。两次阻塞问题里有一次是"修好一个问题、引入另一个"，第二轮校验把它接住了——这正是增量校验 + 全量复检两段式存在的理由。

走查中还发现两个页面缺陷（都已修）：

1. **`row_id` 不在行里**。宿主返回的是 `{rows: [值...], row_meta: [{row_id, version}...]}`，页面却读 `row.row_id` → 永远是 `undefined` → 删除静默失败 → **每点一次「重新校验」，诊断表就多一份重复行**。已改为按下标把 `row_meta` 合并回行；Chromium 回归测试的桩桥也改成真实形状（之前它返回带 `row_id` 的行，正因为与生产不一致才漏掉了这个 bug）。
2. 房间面积按 mm² 原值显示、平面图没有房间标注。现在面积显示 m²，平面图在房间中心标注「名称 · 面积」，`not_verifiable` 的诊断加「待专业确认」标签。

家具、电器与水电点位走查见下方「家具与电器走查」。截图：[`docs/assets/interior-plan-appliances.png`](./assets/interior-plan-appliances.png)。

> 顺带核对：`content-review` 模板正确处理了 `row_meta`；`video-workbench` 与本次修复前的写法相同（`found.row_id` 永远为空，`markDraft` 因此静默 no-op）。它只影响本地草稿记账，本次不修，留作独立问题。

**平台的另一处缺口（非本项目缺陷）**：**没有「新增表」的 HTTP 接口**，只有 Agent 工具 `app_add_table`。所以从模板建出来的应用只有主表 `main`，`rooms` / `diagnostics` / `bom` / `operations` 必须由专家创建；页面在缺表时会明确提示。若希望用户能在 Dashboard 里自己建表，需要平台补一个路由。

### 家具与电器走查（2026-11-27）

在一套 38.9㎡ 的客餐厨（带嵌入橱柜、冰箱、洗衣机、6 个水电点位）上走了一遍：

- **目录生效**：`kind` 用标准名后，页面按品类着色（座椅/储物/桌台/厨房/洗衣），图例自动出现；省略尺寸的家具按目录默认值补全。
- **嵌入关系**：水槽、灶台、烟机用 `host` 挂在台面上，不再误报"互相重叠"，条纹虚线表示嵌入件，标签不重叠。**这是走查抓到的建模问题**：真实橱柜本来就该嵌水槽/灶台，规则一开始把整排橱柜都报成冲突。
- **电气语义**：插座从"家具"升级为 `mep` 点位；`safety.splash_zone` 同时识别 MEP 点位和旧的家具式插座。
- **校验结果**：冰箱前净空 100mm（要求 900，significant）、洗衣机缺排水点（improvement）、通风待专业确认——三条都指向真实问题，没有噪声。
- **调试坑（测试夹具问题，非产品）**：桩桥复用了同一个幂等键，服务端按幂等语义直接返回旧结果，导致看起来"规则改了没生效"。真实 SDK 每次生成新 key。

### 部署步骤（运维视角）

```bash
# 1. 起内核（两个端口，勿混）
python -m octop.infra.design                  # REST 8848：给页面
python -m octop.infra.design --mcp            # MCP  8849：给专家

# 2. 专家侧：设置 → 连接器 → 自定义 MCP
#    transport=streamable_http  url=http://127.0.0.1:8849/mcp  名称如 geometry

# 3. 页面侧：应用详情 → 服务，新增 provider=extension
#    base_url=http://127.0.0.1:8848  mode=write  allow_private_network（需管理员）
#    工具：check_drawing POST /check_drawing、takeoff POST /takeoff
#    并在能力清单勾选 external.write
```

> 用 `POST /apps/from-template`（Dashboard 走的就是这条）建应用时会自动带上模板声明的 `external.write`；**Agent 工具建的应用不会**，必须手动开（见 §6.3）。

---

## 1. 现状事实核对（已在本仓库 / 公开仓库验证）

### 1.1 平台侧（决定架构的硬约束）

| 事实 | 位置 | 影响 |
|---|---|---|
| MiniApp 页面无服务端执行、无网络、无外部脚本 | `src/octop/infra/apps/sandbox.py:31`、`src/octop/infra/apps/models.py:601-785` | 内核必须外置；Three.js 只能内联进来（`<script src>` 被拒；HTML 上限已提到 1 MB，压缩后 ≈600KB 的 three.js 仍挤不进只剩几百 KB 的业务代码空间） |
| HTML ≤ 1MB；素材只收 PNG/JPEG/WebP/GIF；12 表/应用、50 列/表、2 万行/表、单元格 ≤10k 字符 | `src/octop/infra/apps/models.py:33-47` | IR 必须按元素分行存，不能整份塞进一个单元格 |
| 模板只能创建 **一张主表**（表名固定 `main`），其余表要靠 `app_add_table` | `src/octop/infra/apps/service.py:2321-2397` | 主表 = `elements`；`rooms`/`diagnostics`/`bom`/`operations` 由专家创建 |
| 模板的 `services.json` **只能声明只读连接器/外部服务**，不支持 `provider=extension` | `src/octop/infra/apps/service.py:2399-2470`（`_apply_template_services` 只调 `save_service` 的只读参数集） | extension 服务必须由 Owner 在应用详情页手动配置 —— 见 §6.1 |
| extension 服务 `base_url` 走 SSRF 校验，本地/内网地址需 `allow_private_network=true`，**且只有 admin 能开** | `src/octop/infra/apps/service.py:7918-7921` | "MiniApp 连本机微服务"是一次性的管理员配置动作 |
| write 模式：必须 `idempotency_required=true`、`cache_ttl_seconds=0`、不可 `public_live`、至少一个 write tool | `src/octop/infra/apps/service.py:7953-7992` | 微服务必须自己实现幂等键语义 |
| extension 工具：read 只允许 GET/HEAD，write 允许 POST/PUT/PATCH/DELETE | `src/octop/infra/apps/service.py:8075`（`_normalize_extension_tools`） | 校验要传 IR（大 body），只能用 POST → 声明为 **write tool** |
| `external.write` / `external.fetch` 能力位 Agent 设不了，Owner 在应用详情页手动开 | `src/octop/infra/apps/capabilities.py:86-100` | 每用户一次性手动动作，需在专家话术里引导 |
| `custom-mcp` 连接器**明确放行回环/局域网 HTTP** | `src/octop/infra/connectors/custom_mcp.py:118-150` | 专家侧接本机微服务无阻力 |
| AI 任务类型是代码注册表（`summarize_row`/`table_digest`/`pre_review`），有界只读工具循环、单车道串行 | `src/octop/infra/apps/task_types.py:116`、`task_runner.py:45` | 对话修复循环**不要**塞进 AI 任务，走 Agent 工具 |
| 平台已有媒体生成（火山/DashScope/Minimax，Seedream/Seedance） | `src/octop/infra/agents/settings/media_generation.py` | 效果图可行，走图生图（线稿作参考），不回写几何 |

### 1.2 开源锚点（贴文里的原始说法与实际出入）

| 项目 | 实际 | 用法 |
|---|---|---|
| `avalla/llm-interior-designer`（MIT） | 真实。校验清单与 VERIFIED / NON-COMPLIANT / NOT VERIFIABLE / NOT APPLICABLE × blocking/significant/improvement 分级与贴文一致 | **当规则清单和诊断格式模板**。但它是 **FreeCAD 1.1 图形界面 + MCP 插件 + ODA File Converter**，路径面向 macOS，无 headless/CI 模式 → 不能当生产引擎 |
| `laurent512/opencalque`（MIT） | 真实，TypeScript 单体仓。已核实：`schema/document.schema.json`（IR）、`schema/ops.schema.json`（`add_node`/`update_node` 等编辑操作）、`packages/core/src/check.ts` → `checkDrawing()`，ProblemKind 共 8 类（`room-not-enclosed`/`wall-gap`/`walls-overlap`/`opening-not-in-wall`/`opening-past-wall-end`/`openings-overlap`/`fixed-off-wall`/`rooms-in-one-space`）、`packages/core/src/quantities.ts`（工程量）、`packages/cli/src/mcp.ts`（MCP server）、`packages/core/src/check.ts:248` `problemsMadeBy(before, after)`（**增量诊断**） | **最佳蓝本**：IR / 操作 schema / 诊断格式 / 增量校验全部照抄。移植到 Python 约 400–600 行。检查项只有 8 类，通道净宽、座椅间距、采光等 10+ 条需自行补写 |
| `allgpt-co/openlintel` | 真实，但 **AGPL-3.0**（贴文写 MIT 有误），★6，README 自述 "Active development, not a verified hosted product"，能力表明确写"describes development areas, **not a production feature checklist**" | **不可抄进 MIT 的 Octop**。只能当产品流程参考 |
| `wy51ai/floorplan-3d`（MIT，★3955） | 真实，纯 HTML + Three.js | Three.js 压缩后 ≈600KB，只能内联（`<script src>` 被写入期扫描拒），与业务代码合计逼近 1MB 上限 → **不建议直接用**。平面图渲染自己写在模板里 |

---

## 2. IR 契约（微服务与 Octop 的共同接口）

单位统一 **毫米（整数）**，坐标系 y 轴向上，原点在户型左下角。

```jsonc
{
  "version": 1,
  "walls": [
    { "id": "W-001", "x1": 0, "y1": 0, "x2": 3600, "y2": 0, "t": 120 }
  ],
  "openings": [
    { "id": "D-001", "type": "door", "x": 1200, "y": 0, "width": 900, "angle": 90, "host": "W-001" },
    { "id": "N-001", "type": "window", "x": 1800, "y": 0, "width": 1500, "angle": 0, "host": "W-001" }
  ],
  "furniture": [
    { "id": "F-001", "x": 0, "y": 0, "w": 2000, "d": 900, "rot": 0, "kind": "sofa" },
    { "id": "F-002", "x": 3000, "y": 0, "w": 600, "d": 1800, "rot": 0, "kind": "kitchen_counter" },
    { "id": "F-003", "x": 3050, "y": 600, "w": 500, "d": 800, "rot": 0, "kind": "sink", "host": "F-002" }
  ],
  "rooms": [
    { "id": "R-001", "name": "客厅", "polygon": [[0,0],[3600,0],[3600,2400],[0,2400]] }
  ],
  "mep": [
    { "id": "P-001", "type": "socket", "x": 1000, "y": 200, "height": 300 },
    { "id": "P-002", "type": "water", "x": 3400, "y": 700 }
  ]
}
```

- `openings[].type` ∈ `door` / `window` / `sliding_door` / `opening`
- `furniture[].kind` **必须来自品类目录**（`src/octop/infra/design/catalog.py`）：床、座椅、桌台、储物、厨房、洗衣、洁具共 30 余种，带默认尺寸与语义标记；写目录外的名字会报 `catalog.unknown_kind`（`not_verifiable`），不会静默失效。常见别名（`refrigerator` / `washing machine` / `closet`…）会自动归一。
- `furniture[].host`：嵌入关系（水槽在台面里、烟机在灶上）。嵌入链内的重叠不算冲突、净空按被嵌入件计算，支持嵌套（烟机→灶台→台面）。
- `mep[].type` ∈ `socket` / `switch` / `water` / `drain` / `ac` / `gas`。水电点位与家具分开建模；旧的"插座当家具"写法仍然识别，但新方案请用 `mep`。
- 家具省略 `w` / `d` 时按目录默认值补全（`fridge` 700×700、`wardrobe` 1800×600 等）。

---

## 3. MiniApp 数据模型

主表 `main` = **方案元素表**（模板创建），其余四张表由专家用 `app_add_table` 创建。

| 表 | 列 | 说明 |
|---|---|---|
| `main`（elements） | `element_id`(string,必填)、`kind`(enum: wall/opening/furniture/room/material/mep,必填)、`name`(string)、`payload`(text, JSON 几何参数)、`source`(string, 来源：对话轮次/用户上传/默认假设)、`status`(enum: confirmed/tentative/assumption) | IR 单一事实源 |
| `rooms` | `room_id`、`name`、`area_mm2`(number)、`payload`(text, JSON 多边形)、`source` | 房间与面积（由内核算量写回） |
| `diagnostics` | `rule_id`、`severity`(enum: blocking/significant/improvement)、`status`(enum: non_compliant/not_verifiable/not_applicable)、`message`、`elements`(string, 逗号分隔元素 id)、`acknowledged`(boolean) | 诊断清单；页面据此高亮图元 |
| `bom` | `category`、`item`、`qty`(number)、`unit`(string)、`source_element`(string) | 工程量/材料清单 |
| `operations` | `seq`(integer)、`op`(string)、`target`(string)、`args`(text JSON)、`created_at`(datetime) | 操作日志，支持回退与追溯 |

> **为什么不用多张分类型表**：12 张表上限虽然够，但按类型分表会让"一个元素的完整状态"散落多处，`listRows` 拉取与 IR 组装都变复杂。统一 `elements` + `kind` 判别更简单，且天然支持"按 kind 过滤"。

### 3.1 其余四张表的创建规格

模板只能建主表 `main`。其余四张表由装修设计专家在建应用后用 `app_add_table` 创建，列定义必须与下表一致（页面按列名读取）：

```jsonc
// rooms
[{ "name": "room_id", "label": "房间 ID", "type": "string", "required": true },
 { "name": "name", "label": "房间名", "type": "string" },
 { "name": "area_mm2", "label": "面积 (mm²)", "type": "number" },
 { "name": "payload", "label": "多边形", "type": "text" },
 { "name": "source", "label": "来源", "type": "string" }]

// diagnostics
[{ "name": "rule_id", "label": "规则 ID", "type": "string", "required": true },
 { "name": "severity", "label": "严重度", "type": "enum", "required": true,
   "values": ["blocking", "significant", "improvement"] },
 { "name": "status", "label": "状态", "type": "enum",
   "values": ["non_compliant", "not_verifiable", "not_applicable", "compliant"] },
 { "name": "message", "label": "说明", "type": "text" },
 { "name": "elements", "label": "涉及元素", "type": "string" },
 { "name": "acknowledged", "label": "已接受", "type": "boolean" }]

// bom
[{ "name": "category", "label": "类别", "type": "string" },
 { "name": "item", "label": "项目", "type": "string" },
 { "name": "qty", "label": "数量", "type": "number" },
 { "name": "unit", "label": "单位", "type": "string" },
 { "name": "source_element", "label": "来源元素", "type": "string" }]

// operations
[{ "name": "seq", "label": "序号", "type": "integer" },
 { "name": "op", "label": "操作", "type": "string" },
 { "name": "target", "label": "对象", "type": "string" },
 { "name": "args", "label": "参数", "type": "text" },
 { "name": "created_at", "label": "时间", "type": "datetime" }]
```

页面在 `rooms` / `diagnostics` / `bom` 任一缺失时会提示"缺少表，请让专家用 `app_add_table` 创建"，不会静默失败。


---

## 4. 几何内核微服务契约

> **已实现**：内核代码在 `src/octop/infra/design/`，可独立启动（REST / MCP 两种入口），被 `tests/unit/design/` 覆盖。
> 之所以放在 Octop 包内而不在独立仓库：代码要进 `make all` 的 lint / mypy / 测试链路；HTTP + MCP 契约本身就是拆分边界，将来整体搬到独立仓库只需搬包 + 换启动方式。

### 4.1 MCP 工具（专家侧，`custom-mcp` connector 接入）

| 工具 | 入参 | 出参 |
|---|---|---|
| `check_drawing` | `{ir: object, changed_ids?: string[]}` | `{diagnostics: Diagnostic[], checked: int, rules: string[]}` |
| `takeoff` | `{ir: object}` | `{rooms: [...], quantities: [...], floor_area_m2, wall_area_m2, skirting_length_m}` |
| `apply_ops` | `{ir: object, ops: Operation[]}` | `{ir: object, applied: int, rejected: [...]}` |

`apply_ops` 支持 `add` / `update` / `move` / `delete` 四种原子操作，返回新 IR 与被拒操作。内核无状态，幂等由"同输入同输出"保证。

### 4.2 REST（MiniApp 页面侧，extension service）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/check_drawing` | 校验（幂等：同 IR 同结果） |
| POST | `/takeoff` | 工程量 |
| POST | `/ops` | 应用修复 |
| GET | `/health` | 健康检查 + 规则清单 |
| POST | `/jobs` | 长任务（3D 渲染），返回 `job_id` —— **待实现** |
| GET | `/jobs/{job_id}` | 长任务状态与产物 URL —— **待实现** |

启动：

```bash
python -m octop.infra.design                  # REST，127.0.0.1:8848
python -m octop.infra.design --mcp            # MCP，127.0.0.1:8849
OCTOP_INTERIOR_KERNEL_TOKEN=xxx python -m octop.infra.design   # 开启 Bearer 校验
```

### 4.3 Diagnostic 格式（照抄 llm-interior-designer）

```jsonc
{
  "rule_id": "clearance.wardrobe_front",  // 规则 ID，稳定不变
  "status": "non_compliant",               // non_compliant | not_verifiable | not_applicable | compliant
  "severity": "blocking",                  // blocking | significant | improvement
  "message": "衣柜 前方净空 565mm，小于要求的 700mm，被 餐桌 占用",  // 服务端文案，页面直接展示
  "elements": ["F-010", "F-011"],          // 涉及的元素 id，用于页面高亮
  "measured": 565.7, "required": 700       // 可选：实测值与阈值
}
```

### 4.4 已实现的规则

| rule_id | 判据 | 级别 |
|---|---|---|
| `geometry.wall_gap` | 相邻墙端点间隙 ≤ 20mm 才算连通 | blocking |
| `geometry.wall_overlap` | 共线墙段不交叠 | blocking |
| `geometry.room_not_enclosed` | 房间每条边都有对应墙体 | blocking |
| `geometry.opening_not_in_wall` | 洞口中心到墙中心线 ≤ 墙厚 | blocking |
| `geometry.opening_past_wall_end` | 洞口两端在墙段内 | blocking |
| `geometry.openings_overlap` | 洞口沿墙的区间互不相交 | blocking |
| `geometry.element_overlap` | 家具轮廓不相交；墙体不贯穿家具 | blocking |
| `route.continuity` | 各房间经门洞与入口连通 | blocking |
| `route.width` | 100mm 栅格上从入口可达（自由空间内缩 300mm ≈ 600mm 净宽） | blocking |
| `clearance.seat_stand` | 座椅前站立空间 ≥ 750mm（无 `facing` 时取最好一面） | significant |
| `clearance.seat_walk` | 座椅前通行空间 750–900mm | improvement |
| `privacy.path` | 公共区域无需穿过私密房间（按房间名推断） | significant |
| `safety.splash_zone` | 淋浴/浴缸侧 600mm 内无电气件 | blocking |
| `clearance.wardrobe_front` | 衣柜/柜前净空（目录默认 600–700mm） | blocking |
| `clearance.appliance_front` | 电器前净空（冰箱 900、洗碗机/洗衣机 600…） | significant |
| `clearance.sanitary_front` | 洁具前净空 600mm | significant |
| `clearance.kitchen_aisle` | 厨房台面/水槽前通道 900mm | significant |
| `catalog.unknown_kind` | 家具 kind 不在目录里（`not_verifiable`） | improvement |
| `mep.unknown_type` | 水电点位类型未知（`not_verifiable`） | improvement |
| `mep.not_recorded` | 有用水器具但未录入任何点位（`not_verifiable`） | significant |
| `mep.wet_appliance_services` | 用水电器 1500mm 内缺给水/排水点 | improvement |
| `daylight.window_ratio` | 窗面积 ≥ 地面面积 ÷ 8 | significant |
| `programme.min_area` | 房间面积不低于默认值 | significant |
| `robustness.tolerance` | 净空余量 ≥ 20mm | significant |
| `ventilation.service_room` | 恒返回 `not_verifiable` | blocking |

家具与电器的语义来自 `src/octop/infra/design/catalog.py`（类别、默认尺寸、靠墙/湿区/电气/给排水标记、前净空阈值）。`host` 表示嵌入关系（水槽在台面里、烟机在灶上）：嵌入链内的重叠不算冲突，净空按被嵌入件计算，支持嵌套。

`route.width` 用 100mm 栅格 + 300mm 内缩近似"连续 600mm 净宽"，因此存在一个栅格步长的误差；15m×12m、24 件家具的方案实测约 240ms。`privacy.path` 依赖房间名（中英文关键词）判断私密性，房间命名不规范时该规则会跳过而不是误报。

尚未实现的规则（采光细分、门把手净空、入口视线等）在专家的 `skills/interior-checks/SKILL.md` 里标为"待实现"，由专家人工判断并在交付时注明"未经确定性校验"。规则清单与实现状态必须一致——**专家不得把没跑的规则说成已通过**。

### 4.5 增量校验

`check_drawing` 接受可选 `changed_ids`：内核只返回与这些元素相关的诊断（对应 OpenCalque 的 `problemsMadeBy(before, after)`）。这是**修复循环能收敛**的前提，也是"不要把全量 IR 塞回 LLM 上下文"的落地方式。

---

## 5. MiniApp 页面（模板 `interior-plan`）

单文件 HTML，≤1MB，全内联，无外部资源。

| 区块 | 内容 |
|---|---|
| 顶栏 | 标题 + 元素/诊断计数 + 「刷新」「重新校验」 |
| 左：平面图 | 内联 SVG。按 `elements` 画墙（按厚度）、门（门扇 + 弧）、窗、家具（矩形）；`diagnostics` 里 severity≥significant 的元素图元高亮（blocking 红 / significant 橙） |
| 右：诊断清单 | 按严重度分组；显示 rule_id、message、涉及元素 |
| 下：房间与工程量 | 房间面积表 + BOM 表 |

页面动作：
- 「重新校验」→ 读 `elements` 组成 IR → `svc.invoke("check_drawing", {ir})` → 把返回的 diagnostics 写回 `diagnostics` 表 → 重绘高亮。
- 页面**不读像素截图**，也不把诊断结论写回几何；修复由专家/用户决定。

---

## 6. 落地摩擦（必须提前处理）

### 6.1 extension 服务只能手动配置

模板 `services.json` 不支持 `provider=extension`（§1.1）。Owner 需在**应用详情 → 服务**里新增：

```jsonc
{
  "name": "cad",
  "provider": "extension",
  "mode": "write",
  "base_url": "http://127.0.0.1:8848",
  "timeout_seconds": 60,
  "max_request_bytes": 1048576,
  "max_response_bytes": 5242880,
  "idempotency_required": true,
  "cache_ttl_seconds": 0,
  "allow_private_network": true,          // ← 只有 admin 能开
  "write_tools": [
    { "name": "check_drawing", "method": "POST", "path": "/check_drawing", "input_schema": { "type": "object" } },
    { "name": "takeoff",       "method": "POST", "path": "/takeoff",       "input_schema": { "type": "object" } }
  ],
  "confirmation": {}
}
```

同时在应用详情页开启能力位 **`external.write`**（校验走 POST，故用 write 工具；平台 read 工具只允许 GET/HEAD）。

> **缓解方向**（待评估）：给 `services.json` 增加 extension 声明支持，或让 `app_add_service` 支持 extension 模式，使模板能自带服务声明。属于平台增强，非本项目必须项。

### 6.2 专家侧的连接器

专家通过 `custom-mcp` 连接器（`streamable_http`，指向 `http://127.0.0.1:8849/mcp`）拿到内核工具。回环/局域网地址免 SSRF（`custom_mcp.py:149`）。

注意 REST 与 MCP 是两个端口（默认 8848 / 8849），别把 MCP 地址指到 REST 端口上。

**工具是延迟加载的**（真机验证确认）：agent 启动时 `build_mcp_server_configs_for_user` 对 custom MCP 只写空占位并记 `defer custom MCP … load on chat`；真正的工具在首轮聊天前由 `prepare_chat_mcp` 拉取，命名规则是 `{server}_{tool}`（如 `geometry_check_drawing`），并按 `(user, server, spec 指纹)` 缓存复用。所以：

- 第一次对话前工具不在上下文里，**必须真的发一轮消息**才会装载；
- 改了内核地址或工具描述（spec 指纹变化）会触发重新加载。

### 6.3 能力位与权限

- 页面需要 `data.read`（读 IR）、`data.write`（写诊断）、`external.write`（调内核）。
- 三个里 `external.write` 需 Owner 手动开；`data.read`/`data.write` 是新应用默认集。

**已在集成测试与真机验证中确认的细节**：模板 manifest 声明了 `external.write`，只有 **HTTP 创建路径**（`POST /api/apps/from-template`，Dashboard 用的就是它）会按 `grant_template_capabilities=True` 自动带上（`api/routers/apps.py:1856`，真机验证确认返回的 capabilities 含 `external.write`）；**Agent 工具 `app_create_from_template` 建的模板应用不会**获得这个能力位，必须 Owner 手动开。走 Agent 路径时若不手动开，`invoke_service` 直接报 `app does not allow external.write`。

---

## 7. 阶段划分

| 阶段 | 周期 | 交付 | 验收口径 |
|---|---|---|---|
| **0 地基验证** | 1 周 | 最小微服务（`/check_drawing` 返回 8 类检查）+ custom-mcp 接通 + 模板页面能画出一套户型 | 一套真实 80–120㎡ 户型，页面能画出平面图；专家能调通 `check_drawing` |
| **1 布局闭环** | 6–8 周 | 内核补齐通道/人体工学/采光规则 + 算量 + 专家 + 模板 | 10 轮对话内完成布局；blocking 级诊断为零或被显式接受；面积类工程量与手工核算偏差 ≤3%、长度类 ≤5% |
| **2 水电 / 材料** | 4–6 周 | `mep` 元素类型 + 湿区插座等规则 + BOM + 效果图（接平台媒体生成，线稿作参考） | 材料清单可追溯到具体几何元素；效果图不回写几何 |
| **3 多方案与回退** | 3–4 周 | 经济型/舒适型两套 IR 并排、预算差异、任意轮回退 | 对比基于确定性算量，不依赖 LLM 生成对比文字 |

人力：几何/CAD 1 + Agent 后端 1 + 前端 0.5 + QA 0.5 + 装修领域顾问 0.25 ≈ **3.25 人**（比贴文的 4.5 人省下的是 L4 编排层与 Web 复核框架）。

---

## 8. 风险

| 风险 | 控制 |
|---|---|
| 规则写进 Agent 生成的 HTML，随手被改且无测试 | 规则只在微服务一处实现；页面只渲染诊断结果 |
| IR 变更被逐条 HITL 审批拖死 | IR 变更收敛为 `apply_ops` 聚合操作 + 一次确认；不走 42 个 `app_*` 工具的逐条改写 |
| 全量 IR 进 LLM 上下文导致状态膨胀 | 每轮只传原子变更 + 受影响诊断项；内核支持 `changed_ids` 增量校验 |
| 工程量口径与造价方不一致 | 阶段一前用真实项目的工长/造价员核对口径，规则写成可单测的纯函数 |
| 效果图侵蚀确定性内核 | 效果图只改 `MaterialAssignment` 字段后重渲染，输入是已校验几何的线稿 |
| 贴文把 `OpenLintel` 当端到端参照 | 实为 AGPL-3.0 且能力表自述未验证，只当流程参考 |

---

## 9. 未决项

1. ~~**模板能否自动绑定 `custom-mcp` 连接器**~~ —— **已验（2026-11-26）：不能自动绑定，必须每用户手配一次。** 已确认为当前路线唯一的强制手工步骤，见「真机验证」。
2. **是否给 `services.json` / `app_add_service` 增加 extension 支持**——决定 extension 服务是"每应用手动配一次"还是"模板自带"。
3. **IR 体量**：100㎡ 户型 IR JSON 约 100–300KB，对 `max_request_bytes=1MB` 够用，但需实测元素数上限。
4. **家具库**：`furniture[].kind` 的标准清单由谁定（领域顾问）——影响内核与前端渲染的一致性。**注意**：`kind` 缺失会让衣柜/湿区/电气整族规则静默失效，页面侧已有回归测试守着，专家侧需要在 AGENTS.md 的写入规范里继续强调。
5. **验收户型集**：需要 3–5 套真实户型 fixture 作为回归基线。
