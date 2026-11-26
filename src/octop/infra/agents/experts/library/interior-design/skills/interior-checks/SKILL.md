---
name: interior-checks
description: 装修方案的确定性检查清单——每条规则的 rule_id、判据阈值、严重级别与判定方（几何内核自动 / 需人工确认 / 待实现）。用于解释诊断结果、核对交付清单、或回答"这版哪里有问题"。触发词：检查、校验、诊断、规范、净空、通道、采光、隐私、验收。
argument-hint: "<要核对的方案或问题>"
---

# 装修方案检查清单

> 规则的**判定权在几何内核**，不在对话。这份清单用来让你读懂诊断、说清依据、以及判断哪些必须交给专业人员。
>
> **「内核」列是当前实现的真实状态**：`已实现` = 几何内核会算并在诊断里返回；`待实现` = 内核暂不支持，需要你自己按判据人工判断，并在交付时标注"未经确定性校验"；`人工` = 本来就要现场或专业核算。

## 严重级别

| 级别 | 含义 | 你怎么处理 |
|---|---|---|
| `blocking` | 违反硬约束，不改就不能用 | 必须让用户看见并明确接受，不能自己绕过去 |
| `significant` | 明显影响使用体验 | 主动给 2–3 个改法与代价，让用户选 |
| `improvement` | 可以更好，不影响成立 | 列出来即可，别占用对话篇幅 |

## 几何与结构

| rule_id | 检查什么 | 判据 | 级别 | 内核 |
|---|---|---|---|---|
| `geometry.wall_gap` | 墙体之间不断开 | 相邻墙端点间隙 ≤ 20mm | blocking | 已实现 |
| `geometry.wall_overlap` | 墙体不互相重叠 | 共线墙段不交叠 | blocking | 已实现 |
| `geometry.room_not_enclosed` | 房间边界闭合 | 房间每条边都有对应墙体 | blocking | 已实现 |
| `geometry.opening_not_in_wall` | 门窗开洞落在墙上 | 洞口中心到墙中心线 ≤ 墙厚 | blocking | 已实现 |
| `geometry.opening_past_wall_end` | 开洞不超出墙端 | 洞口两端在墙段内 | blocking | 已实现 |
| `geometry.openings_overlap` | 同一墙上的开洞不重叠 | 洞口沿墙的区间互不相交 | blocking | 已实现 |
| `geometry.element_overlap` | 家具互不重叠、不穿墙 | 家具轮廓不相交；墙体不贯穿家具 | blocking | 已实现 |
| `geometry.fixed_off_wall` | 固定设备（冰箱/灶具/洗碗机/洗衣机/马桶/洗手盆/浴缸/台面）贴墙 | 背面距墙 ≤ 墙厚（用户明确要岛台时按用户口径） | significant | 已实现 |

## 通行与净空

| rule_id | 检查什么 | 判据 | 级别 | 内核 |
|---|---|---|---|---|
| `route.continuity` | 门洞是否存在通路 | 各房间经门洞与入口连通 | blocking | 已实现 |
| `route.width` | 连续 600mm 净宽通路 | 自由空间按 300mm 内缩后仍能从入口到达（100mm 栅格） | blocking | 已实现 |
| `clearance.wardrobe_front` | 衣柜、储物柜前净空 | 目录逐类设定（衣柜/储物柜 700、书架/电视柜 600、床头柜 0）；条目显式写 0 表示无需地面净空 | blocking | 已实现 |
| `clearance.appliance_front` | 电器前净空（冰箱 900、洗碗机/洗衣机 600…） | 按目录 `front_clearance` | significant | 已实现 |
| `clearance.sanitary_front` | 洁具前净空（马桶/洗手盆/淋浴/浴缸） | 600mm | significant | 已实现 |
| `clearance.kitchen_aisle` | 厨房操作通道（台面、水槽前） | 900mm | significant | 已实现 |
| `clearance.seat_stand` | 座椅**背后**站立空间 | 人起身是把椅子往后推；未声明朝向取四面最好的一面；< 750mm 报。沙发不参与（靠墙放置） | significant | 已实现 |
| `clearance.seat_walk` | 座椅**背后**通行空间 | 750–900mm 报"可站不可过" | improvement | 已实现 |
| `clearance.pair_min` | 特定家具对的最小间距 | 床–床 600、床–书桌 500、沙发/单椅–茶几 300（默认口径，可按项目覆盖）；未列出的组合不判 | significant | 已实现 |
| `clearance.handle` | 门全开时把手侧净空 | ≥ 100mm（需 `hinge` 说明合页在 start 还是 end）；门扇贴着**平行**侧墙折叠不算问题，只有横向挡住门扇的墙才算 | improvement | 已实现 |

## 采光、通风、面积

| rule_id | 检查什么 | 判据 | 级别 | 内核 |
|---|---|---|---|---|
| `daylight.window_ratio` | 窗地比 | 窗面积 ≥ 地面面积 ÷ 8 | significant | 已实现 |
| `daylight.service_window` | 主要居住房间有直接采光 | 每个房间自己的**外墙**上至少 1 扇窗；内墙上的窗不算 | significant | 已实现 |
| `ventilation.service_room` | 卫生间、厨房通风 | 按当地规范判定 | blocking | 人工（内核返回 not_verifiable） |
| `room.occupancy` | 房间家具占用率 | 10%–40% | improvement | 已实现 |
| `programme.min_area` | 各房间最小面积 | 内置默认值（客厅 12㎡ / 卧室 9㎡ / 厨房 5㎡ / 卫生间 3㎡），随地区变化 | significant | 已实现（默认值可调） |
| `programme.service_empty` | 服务房间不放无关家具 | 厨卫里没有床/沙发/餐桌/衣柜/电视柜 | improvement | 已实现 |

## 安全与隐私

| rule_id | 检查什么 | 判据 | 级别 | 内核 |
|---|---|---|---|---|
| `safety.splash_zone` | 湿区溅水范围内无电气件 | 淋浴/浴缸侧 600mm 内无插座（家具或 MEP 点位都算） | blocking | 已实现 |
| `safety.blocked_egress` | 门扇开启不撞家具 | 90° 开启扫掠范围内没有家具 | blocking | 已实现 |
| `privacy.path` | 共区不穿私密房间 | 从入口到公共区域（客厅/餐厅/厨房/门厅…）不必穿过卧室/卫生间/书房 | significant | 已实现（按房间名推断属性） |
| `privacy.entry_sightline` | 入口不直视床 / 马桶 | 从入户门沿进门方向射线（被墙截断）内没有床或马桶 | significant | 已实现 |

## 人体工学与可施工性

| rule_id | 检查什么 | 判据 | 级别 | 内核 |
|---|---|---|---|---|
| `ergonomics.seat_height` | 座高与桌面高匹配 | 座面到桌面的高度差 250–320mm；只配对 1200mm 内的餐桌/书桌 | improvement | 已实现 |
| `assembly.tall_furniture` | 高柜能否在房间内立起来 | √(短边² + 高²) ≤ 层高；层高取自 `setting` 行，默认 2800 | significant | 已实现 |
| `robustness.tolerance` | 摆放容差鲁棒性 | 净空余量 ≥ 20mm | significant | 已实现 |
| `brief.constraint` | 用户明确提出的约束 | 如"电视不靠窗" | significant | 人工（对话） |
| `requirement.trace` | 每条需求可追溯 | 需求有对应检查项证明 | improvement | 人工 |

## 家具、电器与水电点位

| rule_id | 检查什么 | 判据 | 级别 | 内核 |
|---|---|---|---|---|
| `catalog.unknown_kind` | 家具类型不在品类目录里 | 不在 `catalog.py` 的 kind 列表（含别名） | improvement | 已实现（状态 `not_verifiable`） |
| `mep.unknown_type` | 水电点位类型未知 | 不在 socket/switch/water/drain/ac/gas | improvement | 已实现（`not_verifiable`） |
| `mep.not_recorded` | 有用水器具但没录入任何点位 | `mep` 为空且存在需给排水器具 | significant | 已实现（`not_verifiable`） |
| `mep.wet_appliance_services` | 用水电器缺给水/排水点 | 距器具 1500mm 内必须有对应点位 | improvement | 已实现 |

**嵌入件（`host`）**：水槽在台面里、烟机在灶上，属于正常嵌入。`host` 关系内的重叠不算冲突，净空按被嵌入的件算；**不要为了躲规则把水槽挪出台面**。嵌入可以嵌套（烟机→灶台→台面）。

**阈值可调**：以上净空与半径都是默认值，用户所在地或项目另有要求时按用户要求执行，并在交付里注明依据。

**高度**：家具可写 `h`（毫米），省略时按品类目录的默认高度（衣柜 2200、餐桌 750、椅面 450…）。门窗也可写 `h`（门默认 2100、窗默认 1800），**窗面积按 `width × h` 计算**，所以层高或窗型特殊时要显式写。层高用一条 `kind=setting` 的行记录：`{"ceiling_height": 2600}`，不写按 2800。

**阈值覆盖（把"按用户要求"真正交给内核）**：`kind=setting` 行的 `payload.thresholds` 可覆盖以下键，未写的用默认值：

| 键 | 默认 | 说明 |
|---|---|---|
| `route.width_mm` | 600 | 连续通行的最小净宽 |
| `clearance.seat_stand_mm` / `clearance.seat_walk_mm` | 750 / 900 | 座椅背后站立 / 通行 |
| `clearance.handle_mm` | 100 | 门全开时把手净空 |
| `clearance.storage_front_mm` | 700 | 储物类未逐类声明时的前净空 |
| `front_clearance.<kind>` | 目录值 | 单个品类的净空，如 `front_clearance.fridge`；写 0 = 无需 |
| `daylight.window_floor_ratio` | 8 | 窗地比分母（窗面积 ≥ 地面 ÷ 该值） |
| `room.occupancy_min` / `room.occupancy_max` | 0.10 / 0.40 | 家具占用率区间 |
| `safety.splash_zone_mm` | 600 | 湿区溅水半径 |
| `mep.service_radius_mm` | 1500 | 用水器具到给排水点的最大距离 |
| `programme.min_area.<kind>` | 内置 | 房间最小面积（mm²），kind 为 living/bedroom/kitchen/bathroom/balcony/study |
| `clearance.pair_min.<a>|<b>` | 内置表 | 指定配对的最小间距，如 `clearance.pair_min.bed_double|bed_double` |
| `ergonomics.seat_table_delta_min` / `_max` | 250 / 320 | 座面到桌面的高差区间 |
| `ergonomics.seat_table_reach_mm` | 1200 | 只配对这个距离内的餐桌/书桌 |
| `assembly.tall_item_mm` | 1800 | 超过此高度才检查能否立起 |

**门合页方向**：门可以写 `hinge`（`start` 或 `end`，默认 `start`），决定门扇打开 90° 后把手落在哪一侧；写错会让把手净空判在错误的位置。

**座椅朝向**：座椅/沙发可以写 `facing`（角度，0 朝 +x，逆时针为正）声明使用时的朝向；不写则取四面中最好的一面判定——所以"只有一面被堵"的椅子不会误报，但明显挤在墙角里会报。

**隐私与房间命名**：`privacy.path` 用房间名判断公共/私密（客厅/餐厅/厨房/门厅 vs 卧室/卫生间/书房），所以房间 `name` 要写规范中文或英文名（如"主卧""卫生间"），不要写成"房间1""Room A"。

## 读诊断的规矩

1. **先看 status，再看 severity**。`not_verifiable`（无法验证）和 `not_applicable`（不适用）不是问题，别当问题报给用户——但要在"待专业确认"里提一句。
2. **给实测值和阈值**，不要只说"不符合"。用户要能自己判断能不能接受。
3. **一个 blocking 问题给 2–3 个改法**，每个改法都要有代价（工程量、造价、牺牲了什么）。
4. **不确定是不是问题，就去读实测值**。判定在核心里，别自己目测。
5. **内核没实现的规则**（标"待实现"）：你可以按判据自己判断，但**必须在交付清单里写明"未经确定性校验"**，不要说成"已通过检查"。

## 必须交给专业人员（你不要下结论）

结构改动与承重、燃气、防水与闭水试验、暖通与新风（负荷、风管、坡度）、强弱电点位与线径选型、消防与防火分区、隔声与楼板冲击、物业管理与业委会规范。这些在交付时统一放进"待专业确认"。
