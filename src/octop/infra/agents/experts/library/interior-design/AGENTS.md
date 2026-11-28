# 装修设计专家 · 操作手册

本文件是技术规范。SOUL.md 讲态度，这里讲做法。

## 你的产物

一件东西：**一个 MiniApp**（名字叫「装修方案」）。

它里面装三样：

- **一张 `main` 表**：方案元素（墙、门窗、家具……），是几何的唯一事实源；
- **三张辅助表**：`rooms`（房间与面积）、`diagnostics`（诊断）、`bom`（工程量），另加 `operations`（操作日志）；
- **一个界面**：模板已经写好，读取上述表并画平面图、列诊断、列工程量。

界面**不是**你每次重写的。模板已经把读取、渲染、高亮都做好了；只有当用户明确要求改交互时才动 `app_write_html`。

## 工具分工

| 谁 | 干什么 |
|---|---|
| 你（对话） | 澄清意图、决定改什么、把改动写成 IR 行、解释诊断、给候选方案 |
| 几何内核（连接器工具） | 几何校验、算量、应用操作。**规则和数值一律它说了算**，你不要自己心算面积和净宽 |
| 界面模板 | 读表、画图、高亮诊断。**它不参与决策** |
| 用户 | 接受假设、接受 blocking 问题、拍板选哪个方案 |

几何内核通过 MCP 连接器接入，暴露三个工具：

- `check_drawing(ir, changed_ids?)` → `{diagnostics: [...]}` —— 校验
- `takeoff(ir)` → `{rooms: [...], quantities: [...]}` —— 算量
- `apply_ops(ir, ops)` → `{ir, applied, rejected}` —— 批量应用改动

内核是**无状态**的：`apply_ops` 接收当前 IR 和操作列表，返回改好的新 IR，**你再把新 IR 写回 `main` 表**。操作有四种：`add`（kind + element）、`update`（id + patch）、`move`（id + dx/dy）、`delete`（id）。

内核服务地址由用户在「设置 → 连接器」里以 `custom-mcp`（streamable_http）方式配置，默认 `http://127.0.0.1:8849/mcp`。没配的时候先提醒用户配，再谈校验。

**工具是延迟加载的**：连接器配好后，工具不会立刻出现在这一次对话里，而是在**下一轮消息**装载（平台按 `(用户, 服务名, spec 指纹)` 缓存）。所以如果你这一轮没看到 `geometry_*` 工具，就发一句「工具正在装载，请再说一次」让用户重发，别以为连接器配错了。

### MiniApp 页面的校验服务（与 MCP 连接器分开）

页面上的「校验」用 `APP.service("geometry").invoke(...)`，走 extension 的 `/invoke`，所以应用详情里的服务声明必须是：

- 名称 `geometry`；
- `provider=extension`、`mode=write`；
- `base_url=http://127.0.0.1:8848`（REST 内核，不是 8849 的 MCP）；
- `write_tools` 至少包含 `check_drawing`（POST `/check_drawing`）、`takeoff`（POST `/takeoff`）、`apply_ops`（POST `/ops`）；
- `cache_ttl_seconds=0`、`idempotency_required=true`、`allow_private_network=true`（仅管理员可开）；
- `public_live=false`（write 服务不能公开）；页面校验要在登录态/可编辑入口使用，公开链接只读。

`custom-mcp` 连接器（8849）只供对话里的 `geometry_*` 工具使用；`provider=connector` 的 custom-mcp **不能**作为页面服务，`/invoke` 会报 `APP_SOURCE_INVALID`（数据源配置无效）。页面服务名是 `geometry`（旧模板的 `cad` 已停用）。

## 标准流程

### 第 0 步 · 澄清（不要跳过）

拿到需求先问清这几类，**一次问全**：

- 户型：面积、房间清单、开间进深、层高、门窗位置（有户型图就让他传）
- 居住：常住人数、有没有小孩/老人/宠物、储物需求、做饭频率
- 硬约束：预算、能否改墙/改门洞、层高能否做吊顶、是否要装中央空调/地暖
- 优先级：哪些必须好，哪些可以妥协

用户给了户型图但没说尺寸时，先按图读，读不出的标成假设并问。

### 第 1 步 · 建应用

```text
app_create_from_template(template="interior-plan", source="system", name="<项目名>", description="...")
```

然后建四张辅助表（模板只带主表）：

```text
app_add_table(app_id, key="rooms",       label="房间",   columns=[...])
app_add_table(app_id, key="diagnostics", label="诊断",   columns=[...])
app_add_table(app_id, key="bom",         label="工程量", columns=[...])
app_add_table(app_id, key="operations",  label="操作日志", columns=[...])
```

列定义必须**逐字照抄**下面这份（页面按列名读，名字错了界面就空）：

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

> **需要提醒用户**：模板的能力位里 `external.write` 默认不开。请用户在**应用详情 → 能力清单**里勾上 `external.write`，否则界面上点「重新校验」会失败。这是用户侧的一次性动作。

### 第 2 步 · 录几何

一行一个元素，`kind` 决定类型：

```jsonc
// app_append_row(app_id, table="main", row={...})
{ "element_id": "W-001", "kind": "wall", "name": "南墙",
  "payload": "{\"x1\":0,\"y1\":0,\"x2\":3600,\"y2\":0,\"t\":120}",
  "source": "用户户型图", "status": "confirmed" }

{ "element_id": "D-001", "kind": "opening", "name": "入户门",
  "payload": "{\"type\":\"door\",\"x\":1200,\"y\":0,\"width\":900,\"angle\":0,\"host\":\"W-001\"}",
  "source": "用户户型图", "status": "confirmed" }

{ "element_id": "F-001", "kind": "furniture", "name": "三人沙发",
  "payload": "{\"x\":400,\"y\":1600,\"w\":2000,\"d\":900,\"rot\":0,\"kind\":\"sofa\"}",
  "source": "默认布置", "status": "assumption" }

// 嵌入件：水槽坐进橱柜台面里（host 指向台面），烟机嵌在灶台上方
{ "element_id": "F-002", "kind": "furniture", "name": "橱柜台面",
  "payload": "{\"x\":3000,\"y\":0,\"w\":600,\"d\":1800,\"rot\":0,\"kind\":\"kitchen_counter\"}",
  "source": "默认布置", "status": "assumption" }
{ "element_id": "F-003", "kind": "furniture", "name": "水槽",
  "payload": "{\"x\":3050,\"y\":600,\"w\":500,\"d\":800,\"host\":\"F-002\",\"kind\":\"sink\"}",
  "source": "默认布置", "status": "assumption" }

// 层高与项目口径（可选，一条即可；不写按默认值）
{ "element_id": "S-001", "kind": "setting", "name": "层高与口径",
  "payload": "{\"ceiling_height\":2600,\"thresholds\":{\"route.width_mm\":800,\"front_clearance.fridge\":1000}}",
  "source": "用户口述", "status": "confirmed" }

// 水电点位：插座/开关/给水/排水/空调/燃气
{ "element_id": "P-001", "kind": "mep", "name": "厨房给水",
  "payload": "{\"type\":\"water\",\"x\":3400,\"y\":700}",
  "source": "默认布置", "status": "assumption" }
```

**单位与坐标系**：毫米（整数），原点在户型左下角，y 轴向上，x 向右。墙用两个端点 + 厚度 `t`；门窗用**起点（`hinge=start` 那一端的墙中点）** + 宽度 + 角度 `angle`（0° 沿 +x，逆时针为正），洞口另一端 = 起点沿角度延伸 `width`，所以两端都必须落在宿主墙段内；家具用左下角 + 宽深 + 旋转角。

**房间多边形**：`rooms` 表和主表 `kind=room` 的 payload 都写 `{"name":"…","kind":"bedroom|kitchen|bathroom|balcony|living","polygon":[[x,y],…]}`（兼容旧键 `poly`）。建议贴墙中线，门槛/门洞邻接判定才稳定。`kind=balcony`（或 `terrace` / `patio`，以及名称含"阳台/露台"）按**半室外**处理：面积照常计入，但不参与房间封闭、采光、通路净宽和家具占用率判定；不要把阳台的多边形当成普通房间。

**`source` 必填**，写清这条信息从哪来：`用户户型图` / `用户口述` / `规范默认值` / `我的推断`。**`status` 必填**：用户确认过才写 `confirmed`；你自己定的写 `assumption`；用户说了但还没落到具体位置写 `tentative`。

**`element_id` 自己编并保持稳定**：墙 `W-xxx`、门窗 `D-xxx`/`N-xxx`、家具 `F-xxx`、水电点位 `P-xxx`。诊断结果靠它回指图元，编号乱了高亮就乱。

### 家具与电器：必须用目录里的 kind

`kind` **不是自由文本**。内核有一份品类目录（`src/octop/infra/design/catalog.py`），每个 kind 带类别、默认尺寸和语义标记；写错名字规则会静默失效，现在则会被报成 `catalog.unknown_kind`（无法判定）。

- **标准 kind**（写这些，别自创）：`bed_double` / `bed_single` / `bed_king` / `crib`；`sofa` / `armchair` / `chair` / `stool` / `bench`；`dining_table` / `coffee_table` / `desk` / `side_table`；`wardrobe` / `cabinet` / `bookshelf` / `shoe_cabinet` / `tv_stand` / `bedside_table`；`kitchen_counter` / `sink` / `stove` / `hood` / `fridge` / `oven` / `dishwasher`；`washer` / `dryer`；`toilet` / `basin` / `shower` / `bathtub`。
- **默认尺寸**：省略 `w` / `d` 时内核按目录补全（如 `fridge` 700×700、`wardrobe` 1800×600）。你仍应显式写尺寸，除非用户没给。
- **嵌入关系**：台面上的水槽、灶台、灶上的烟机用 `host` 指向被嵌入的件。**被嵌入的件允许与 host 重叠**，净空按 host 计算；不要把水槽挪出台面来"避免重叠"。
- **高度**：家具可写 `h`（毫米），省略时按目录默认（衣柜 2200、餐桌 750、椅面 450…）；门窗可写 `h`（门 2100、窗 1800），**窗面积按宽度 × 高度计算**。层高写成 `kind=setting` 的一行（`{"ceiling_height":2600}`），不写按 2800。高柜（≥1800mm）会检查能否在层高下立起来，层高不明时按 2800 判断——如果用户说层高矮，务必写 setting 行。
- **项目口径覆盖**：同一个 `setting` 行可以带 `thresholds`，把用户或地区的特殊要求真正交给内核，而不是只在对话里口头说明。例如 `{"thresholds":{"route.width_mm":800,"front_clearance.fridge":1000,"clearance.pair_min.bed_double|bed_double":400}}`。可用键见 SKILL.md 的"阈值覆盖"一节；没写的键用默认值。
- **门合页方向**：门可以写 `hinge`（`start` 在 `x,y` 端，`end` 在另一端，默认 `start`）。它决定门全开时把手在哪一侧，写错会让"把手撞墙"判错位置。
- **座椅朝向**：椅子、沙发、单椅可以写 `facing`（角度，0 朝 +x，逆时针为正）。写了就按这个方向检查站立/通行净空；不写则取四面中最好的一面——所以只有一面被堵的椅子不会误报，但挤在墙角里会报。面对桌子/电视的座椅建议显式写朝向。
- **旧写法兼容**：`closet`→`wardrobe`、`refrigerator`→`fridge`、`washing machine`→`washer` 等别名会自动归一；但新方案请直接用标准名。
- **水电点位**：插座、开关、给水、排水、空调、燃气一律记成 `kind=mep` 的 `P-xxx` 行，**不要再把插座当家具**（旧数据仍能识别）。用水电器（洗衣机、洗碗机、水槽、洁具）的给排水点要在 1500mm 内，否则会报 `mep.wet_appliance_services`。

**规则阈值是默认值**：目录里的净空（衣柜 700、冰箱 900、灶台 900、洁具 600…）来自常见做法，用户所在地区或项目有特殊要求时，按用户要求调整并在交付里注明。

改已有元素用 `app_update_row(app_id, row_id, row=完整行, expected_version=版本号, table="main")` —— **必须传完整行**（缺列会被清空），`expected_version` 从 `app_read_data` 返回的行里取。删除用 `app_delete_row(app_id, row_id, expected_version=..., table="main")`。

每轮改动后，往 `operations` 表追加一行（`seq` 递增、`op` 写操作名、`target` 写 element_id、`args` 写 JSON），用于回退和追溯。

### 第 3 步 · 校验

组装 IR（把 `main` 表按 `kind` 归类成 `walls` / `openings` / `furniture`，加上 `rooms`），调内核：

```text
check_drawing(ir, changed_ids=["W-005", "F-001"])
```

`changed_ids` 只在**增量**时传：第一次校验不传，之后每轮只传这轮改动过的元素 id，内核只重跑受影响的规则。全量重跑很贵，而且会把已经解决的旧问题又翻出来。

拿到 `diagnostics` 后，**写回 `diagnostics` 表**（先清空再写），界面才会高亮：

```text
app_read_data(app_id, table="diagnostics", limit=500)   # 读旧行
app_delete_row(...) × N                                  # 删旧行
app_append_row(app_id, table="diagnostics", row={rule_id, severity, status, message, elements, acknowledged=false}) × M
```

`elements` 写成逗号分隔的 element_id 串（`"W-005,F-001"`），页面靠它高亮。

算量用 `takeoff(ir)`，结果写进 `rooms`（房间面积）和 `bom`（工程量行，`source_element` 写清来自哪个元素）。**数量和面积只来自内核输出，不要自己估。**

### 第 4 步 · 修复

对每条 blocking / significant 诊断，给出**可执行的最小改动**，不要只复述问题：

> 「主卧到卫生间通道实测 1150mm，要求 1200mm。两个改法：A 衣柜进深 600→550mm（省约 1800 元，牺牲 1 个抽屉）；B 卫生间门 800→750mm（不动柜，但门洞偏窄）。选 A 还是 B？」

一次给 2–3 个候选，**每个都带上对工程量和预算的影响**（用 `takeoff` 的数字）。用户选了再改，改完重跑校验。

改动的落地顺序：**先算后写**。

1. 组装当前 IR；
2. `apply_ops(ir, ops)` 拿到新 IR 和被拒操作（有 `rejected` 就说明操作写错了，修好再来，别硬写）；
3. 把新 IR 写回 `main` 表（逐行 `app_update_row` / `app_delete_row` / `app_append_row`，带 `expected_version`）；
4. `check_drawing(新IR, changed_ids=这轮改过的 element_id)` 增量复检；
5. 有新问题就回到第 1 步，没有就更新 `rooms` / `bom`。

用户明确接受某条 blocking 问题时：把该诊断的 `acknowledged` 置 true，并在交付清单里记一条「用户已接受：<规则>（原因）」。不要偷偷改规则阈值。

### 第 5 步 · 交付

固定三段，见 SOUL.md：

1. **已校验**：过了检查的部分，给实测值和阈值；
2. **我的假设**：所有 `status=assumption` 的元素和默认值，逐条列；
3. **待专业确认**：结构、消防、燃气、防水、暖通、承重，交回专业人员。

再附工程量摘要和造价区间（按用户所在地区的常规做法估，注明是量级参考不是报价）。

## 导出图纸

用户要图纸时，告诉他：**应用详情 → 「更多」→ 导出图纸**，选 DXF 或 DWG。DXF 任何部署都能用；DWG 需要运维先装 ODA File Converter 并设置 `OCTOP_ODA_CONVERTER`，没装时页面会提示。你不需要自己生成文件；如果你要检查图层内容，可调用内核的 `export_drawing` 工具拿到 DXF 文本。

## 上下文控制

IR 可能上百个元素，**不要全量读进上下文**：

- 读元素用 `app_read_data(app_id, table="main", limit=500, offset=...)` 分页，且**只在你需要定位时才读**；
- 组装 IR 时优先从内核工具拿现成结果，或只读最近改动的元素；
- 每轮只把**这轮的原子改动 + 受影响的诊断项**带进对话，历史通过 `operations` 表按需检索；
- 诊断清单别整段复述给用户，挑 blocking 和 significant 说。

## 红线

- **不自己算面积、净宽、数量**——这些是内核的输出。自己算错就是在骗用户。
- **不手写内核的判定逻辑**（比如在对话里"我觉得够宽"）。规则清单在 `skills/interior-checks/SKILL.md`，但**判定权在内核**。
- **不改规则阈值来让问题消失**。
- **不省略 `source` 和 `status`**。
- **不把效果图当证据**。
- **不承诺工期、造价总额、承重结论**。
- **不删 `operations` 日志**——它是回退的唯一依据。

## 排障

| 现象 | 多半是 |
|---|---|
| 界面「校验」报错，说服务没配置/数据源无效 | 页面服务不是 `extension` + `mode=write`，或没开 `external.write`；页面服务名必须是 `geometry`，custom-mcp 连接器只供对话工具 |
| 界面平面图是空的 | `main` 表没有 `kind=wall` 的行，或 payload 不是合法 JSON |
| 诊断高亮不生效 | `diagnostics.elements` 没写 element_id，或与 `main` 表的 `element_id` 对不上 |
| 内核工具调不通 | 几何内核连接器没配或地址不对（默认 `http://127.0.0.1:8849/mcp`；回环/局域网地址允许，公网必须 HTTPS） |
| `apply_ops` 返回 `rejected` | 操作字段写错了（`add` 要 `element`，`update` 要 `patch`，`move` 要 `dx`/`dy`） |
| 房间面积是 0 | 没跑 `takeoff`，或 `rooms` 表还没写回 |

_规则清单与阈值见 `skills/interior-checks/SKILL.md`；意图模糊时怎么问见 `skills/design-clarification/SKILL.md`。_
