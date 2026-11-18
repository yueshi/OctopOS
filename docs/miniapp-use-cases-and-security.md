# MiniApp 使用场景、开发方式、技能与安全边界

> 日期：2026-11-17
> 分支：`feature/miniapp-capabilities`
> 口径：以当前分支代码为准。[MiniApp 产品定位与路线图](./miniapp-product-plan.md) 的 P0 清单部分已滞后（行级历史、reference 列、aggregate 在当前代码中已存在），本文按代码重新核对。
> 关系：补充 [MiniApp 功能与架构现状](./miniapp-overview.md)、[MiniApp 产品定位与路线图](./miniapp-product-plan.md)、[MiniApp 与 Magic 的参考](./miniapp-magic-reference.md)。

---

## 0. 结论

1. **MiniApp 最适合八类场景**：台账 CRUD、登录态表单/收集、轻看板、审核/审片、AI 轻应用、外部数据速览、公开只读分享、小工具/展示页。多表关联与行级历史在当前分支已可用。
2. **开发一个小程序的标准路径是**：定场景 → 定数据模型 → `app_create` → `app_write_html` → 灌数据/核对 → 封面/交付 → 按需发布。页面只通过 `octopApp` SDK 与宿主交互，不写后端、不发网络请求。
3. **需要一组技能，但不是越多越好**：建议 1 个核心 `miniapp-builder` + 1 个 `miniapp-security-review` + 1 个可选 `miniapp-data-import`，把现在 471 行的 `light-apps/AGENTS.md` 拆成「始终加载的红线 + 按需加载的流程 + references」。
4. **安全不能靠 prompt 或技能**：真正的边界是「浏览器沙箱 + 宿主能力桥 + 服务端授权 + 发布/审计闸门」四层。技能只保证 Agent 正确使用，不能保证它不滥用；用户自己写 HTML 也逃不出去，因为页面没有 JWT、没有网络、没有任意宿主 API。

### 0.1 状态与已知缺口（2026-11-17）

| 项 | 状态 | 说明 |
|---|---|---|
| 应用目录写保护 + 读取/发布复验 | ✅ 已完成 | Agent 通用文件工具不能写 `apps/app_*/**`；`GET /html`、公开页、`publish` 快照都会复验 HTML。 |
| 破坏性/公开操作 prepared operation | ⏸ 暂缓（已知缺口） | 删列 / 清 PII / `include_pii` 导入 / 公开分享目前仍靠 `AGENTS.md` 提示与工具描述，没有服务端绑定「用户批准的是哪份 payload」。补偿：`app_delete` 强制 HITL、应用备份/恢复、`sharing_notice` 提示；但**不能把它当作已覆盖**。重做触发条件：对外公开、多用户、PII、GA。 |
| page-facing 高风险端点统一 `require_capability` | ⏸ 暂缓（已知缺口） | 当前靠前端桥白名单 + `generate`/`service`/`events.emit` 的服务端检查；数据 CRUD 走 ACL/行 scope。风险是未来新增桥方法或旧应用 legacy all-on 漏检。重做触发条件：新增桥方法、系统应用能力、引入 page-capability token。 |

> 这两项是**有意暂缓的风险**，不是「已实现」。在对外分享、多人协作或处理 PII 前，至少按 §5 的低成本补偿做；不要用本文档的「四层防线」描述推断它们已经存在。

---

## 1. MiniApp 使用场景

### 1.1 最匹配的场景

| 场景 | 典型例子 | 数据模型 | 当前支持 |
|---|---|---|---|
| **台账 / CRUD** | 门店巡检、库存、客户跟进、工单、供应商名录 | 1–12 张表，行级 CRUD、CAS、行级历史、CSV 导入导出 | ✅ 完整 |
| **登录态表单 / 收集** | 内部报名、反馈表、线索登记、检查表 | 表单写行，按用户/部门做行级可见性 | ✅ 登录态可用；匿名写入未做 |
| **轻看板** | KPI 卡、趋势图、分类占比、排行榜 | 单表/少表，`aggregate` + 手写 SVG/CSS 图表 | ✅ 完整（服务端聚合） |
| **审核 / 审片** | 内容审核、素材确认、发布前检查 | 行状态 + 行级历史 + 资产预览 + Agent 建议 | ⚠️ 部分：页面缺上传/媒体预览 |
| **AI 轻应用** | 摘要、改写、分类、翻译、单次生成 | 页面输入 → `octopApp.generate()` → 展示 | ✅ 有 `ai.generate`（owner 开启） |
| **AI 工作流前端** | 报告生成、数据分析、批量内容生产 | 页面收集输入 → Agent 多步执行 → 回显结果 | ❌ 缺应用发起 Agent 任务 |
| **外部数据速览** | 连接器数据、第三方 API、缓存看板 | owner 声明只读 service，页面 `service().call()` | ✅ 有 `external.fetch`（owner 开启） |
| **公开只读分享** | 对外台账、公示页、活动页 | 发布快照 + 公开表 + PII 脱敏 | ✅ 只读快照；有有效期 |
| **小工具 / 展示页** | 计算器、报价器、投票展示、方案页 | 无数据或纯前端；`capabilities=[]` | ✅ 完整 |
| **多表关联** | 订单 + 客户、巡检 + 门店 | `reference` 列 + `expandRefs` + 聚合 | ✅ 当前分支已实现 |

### 1.2 当前代码里已落地、但文档容易误判的能力

- **行级变更历史**：`light_app_row_history` 迁移（`src/octop/infra/db/migrations/040_light_app_row_history.sql`）、`AppService.row_history()`（`src/octop/infra/apps/service.py:4268`）、`GET /api/apps/{app_id}/rows/{row_id}/history`。
- **reference 列**：`COLUMN_TYPES` 含 `reference`，`ref_table` / `ref_label`、写入时校验目标行存在、读取时解析 label（`src/octop/infra/apps/models.py:58`、`service.py:2478`）。
- **服务端聚合**：`aggregate.py`、`GET /api/apps/{app_id}/aggregate`、SDK `octopApp.aggregate()`。
- **行级写入审计**：行历史记录 actor/source，支持保留期清理。
- **公开快照与有效期**：`light_app_publications`、`expires_at`、`ttl_days`。

仍缺的产品能力：

- **匿名写入 / 公开表单提交**：公开路由只有 GET（`src/octop/api/routers/public_apps.py`）。
- **页面附件上传**：SDK 没有 `upload()`；`/{app_id}/upload` 是导入通道，不是页面通道。
- **应用发起 Agent 任务**：只有 `composeMessage` 预填输入框和一次性 `generate`。
- **分享密码 / 访问统计**：有 token、过期、快照；没有密码保护和公开访问统计。
- **应用级 Agent 会话 / companion skill**：没有 `@miniapp` 可信 mention 与来源 project 技能加载。

### 1.3 不适合 MiniApp 的场景

| 不适合 | 原因 | 应该用什么 |
|---|---|---|
| 多页 React / 复杂路由 / 包管理器 | MiniApp 是单页、无构建链 | React 页面 / vibe-coding |
| 重型 BI、跨表 join、钻取 | 只有单表聚合与 reference 展开 | 原生表 + 后端查询 |
| 流程编排 / 审批流 | MiniApp 不是流程引擎 | `infra/cron`、`infra/events`、Teams |
| 逐帧视频剪辑 / 原生性能 | 沙箱与单文件限制 | 原生应用 / 桌面工具 |
| 高并发、强事务、任意后端 API | 单实例、行级 CAS、无自定义服务端 | 独立后端服务 |
| 需要直接持有密钥的外部调用 | 页面不允许持有凭据 | 连接器 / 宿主 `service` / Agent |

---

## 2. 如何开发一个小程序：门店巡检台账

### 2.1 需求与数据模型

用户说：「帮我做一个门店巡检台账，记录每家店每周的卫生、陈列、库存、安全检查结果，能按门店和结果筛选，顶部显示未通过数量。」

对应 MiniApp：

- 场景：台账 / CRUD + 轻看板。
- 表：主表 `main`。
- 字段：

```json
[
  { "name": "store", "label": "门店", "type": "string", "required": true },
  { "name": "inspected_on", "label": "巡检日期", "type": "date", "required": true },
  { "name": "item", "label": "检查项", "type": "enum", "required": true,
    "values": ["卫生", "陈列", "库存", "安全"] },
  { "name": "result", "label": "结果", "type": "enum", "required": true,
    "values": ["通过", "不通过"] },
  { "name": "owner", "label": "负责人", "type": "string" },
  { "name": "note", "label": "备注", "type": "text" }
]
```

### 2.2 用 light-apps 专家创建

推荐走 `light-apps` 专家（工具在 `src/octop/infra/apps/tools.py`）：

| 步骤 | 工具 | 说明 |
|---|---|---|
| 1 | `app_create` | 建应用 + 主表；只传低风险 capabilities，默认安全集即可 |
| 2 | `app_write_html` | 写自包含 HTML；写入前会扫描外链/iframe/fetch 等 |
| 3 | `app_append_row` | 灌几条真实数据，帮助界面贴近实际 |
| 4 | `app_read_data` | 核对条数、字段、取值 |
| 5 | `app_set_preview` | 画一张 1200×630 的 SVG 封面 |
| 6 | `app_open` | 首个完整可用版本完成后调用一次，生成聊天卡片入口 |
| 7 | `app_share_link` | 需要对外只读时再调用；默认复用同一个链接 |

关键约束：

- 字段只能追加，不能改名/删除；定字段前先想清楚。
- 结构变更用 `app_alter_schema`（改名/删列/改类型），有数据时可能整次拒绝。
- 追加字段用 `app_update_schema`；已有数据时不能加必填字段。
- 多表用 `app_add_table`；主表 `main` 永远存在，每应用最多 12 张表。
- 删列会永久丢数据，必须先向用户确认。

### 2.3 页面最小骨架

```html
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>门店巡检台账</title>
<style>
  body { font-family: system-ui, -apple-system, "PingFang SC", sans-serif; margin: 24px; }
  .kpi { display: flex; gap: 16px; margin-bottom: 16px; }
  .card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 16px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 8px; border-bottom: 1px solid #eee; }
  .fail { color: #b91c1c; font-weight: 600; }
</style>
</head>
<body>
<h1>门店巡检台账</h1>
<div class="kpi">
  <div class="card">总记录 <strong id="total">0</strong></div>
  <div class="card">未通过 <strong id="failed" class="fail">0</strong></div>
</div>
<label>门店 <select id="store"><option value="">全部</option></select></label>
<label>结果 <select id="result">
  <option value="">全部</option><option>通过</option><option>不通过</option>
</select></label>
<table>
  <thead><tr><th>门店</th><th>日期</th><th>检查项</th><th>结果</th><th>负责人</th></tr></thead>
  <tbody id="rows"></tbody>
</table>
<script>
const state = { store: "", result: "" };

async function render() {
  const filters = {};
  if (state.store) filters.store = state.store;
  if (state.result) filters.result = state.result;

  const { rows, total } = await octopApp.listRows({ limit: 200, filters });
  document.getElementById("total").textContent = total;

  const failed = await octopApp.aggregate({
    groupBy: "result", agg: "count", filters: { result: "不通过" }
  });
  document.getElementById("failed").textContent =
    failed.groups[0] ? failed.groups[0].count : 0;

  const tbody = document.getElementById("rows");
  tbody.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.store)}</td>
      <td>${escapeHtml(row.inspected_on)}</td>
      <td>${escapeHtml(row.item)}</td>
      <td class="${row.result === "不通过" ? "fail" : ""}">${escapeHtml(row.result)}</td>
      <td>${escapeHtml(row.owner || "")}</td>
    </tr>`).join("");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}

document.getElementById("store").addEventListener("change", (e) => {
  state.store = e.target.value; render();
});
document.getElementById("result").addEventListener("change", (e) => {
  state.result = e.target.value; render();
});
render();
</script>
</body>
</html>
```

这段骨架体现了几条硬规则：

- 所有 CSS/JS 内联，不引 CDN、远程字体、远程图片。
- 不写 `fetch()`；数据只走 `octopApp.listRows()` / `aggregate()`。
- 用 `textContent` 或转义后的字符串渲染，不要直接把用户数据拼进 `innerHTML`。
- 列表用服务端过滤与聚合，不把全表拉到前端算。
- 有外部链接时用 `octopApp.openExternal(url)`，不要写 `<a href="https://…">`。

### 2.4 灌数据、封面、交付、发布

```python
# 伪代码：light-apps 专家工具序列
app = app_create(name="门店巡检台账", columns=[...])
app_write_html(app_id=app["app_id"], html=html)
app_append_row(app_id=app["app_id"], row={
    "store": "A 店", "inspected_on": "2026-11-17",
    "item": "卫生", "result": "通过", "owner": "小林"
})
app_read_data(app_id=app["app_id"], limit=20)
app_set_preview(app_id=app["app_id"], svg=cover_svg)
app_open(app_id=app["app_id"])          # 只调一次
app_share_link(app_id=app["app_id"])    # 需要对外时
```

发布注意：

- `app_share_link` 冻结当前 HTML 和公开表结构；之后改 HTML/schema 不影响已分享链接，要重新发布。
- 公开链接默认只展示主表；附加表默认私有。需要公开时用 `app_set_table_visibility`。
- 只公开部分行时，建独立公开表，不要靠页面过滤隐藏数据。
- PII 列在公开页自动脱敏；不要把敏感信息塞进备注字段。

### 2.5 两条开发路径

| 路径 | 适用 | 特点 |
|---|---|---|
| **工作台快速模式 / 模板** | 用户有 Excel/CSV，或想要标准看板 | 确定性生成、不走 LLM、秒级；之后可让专家继续改 |
| **light-apps 专家对话** | 需求需要澄清、要定制界面/多表/AI | 灵活；需要计划、工具调用和验收 |

---

## 3. 是否需要生成一组技能

### 3.1 现状

`src/octop/infra/agents/experts/library/light-apps/AGENTS.md` 已经是一份 **471 行**的完整手册，覆盖：

- 20+ 个 `app_*` 工具与约束；
- 页面能力位、沙箱红线、CSP；
- SDK 用法（`octopApp`）；
- 台账导入、多表拆分、公开表、发布快照；
- IM 快速路径与输出纪律。

它在专家启动时作为 `prompt_files` 始终加载（`manifest.json` 的 `prompt_files: ["SOUL.md", "AGENTS.md"]`），没有 skill 的按需加载机制。

### 3.2 问题

1. **上下文成本高**：简单任务也要带着 471 行工具/导入/发布说明。
2. **不可复用**：主 Agent、其他专家、团队子 Agent 想创建/修改 MiniApp 时，拿不到这套规则。
3. **职责混杂**：工具手册、数据建模、安全红线、IM 输出纪律混在一个文件里，维护和 review 都困难。
4. **安全规则和业务规则同权**：关键红线应该始终加载，而详细流程可以按需加载。
5. **部分描述已过时**：manifest 里还写「数据存在应用自己的 CSV 里」，当前 v2 行数据以 DB 为准；CSV 是工作区副本/迁移遗留。

### 3.3 建议的技能集：1 个核心 + 1 个安全审查 + 1 个可选导入

| Skill | 触发 | 内容 | 加载方式 |
|---|---|---|---|
| **`miniapp-builder`** | 任何「创建/修改/发布小程序」任务 | 场景选择、数据建模、创建流程、HTML/SDK、能力位、封面、交付、发布 | 核心 skill，按需加载；references 再细分 |
| **`miniapp-security-review`** | 发布/分享、安装模板、开启高风险能力、引入外部 service、用户问「安全吗」 | 能力差异、外部引用、PII、公开表、webhook、service 范围、扫描结果、结论报告 | 按需加载；发布前强制 |
| **`miniapp-data-import`**（可选） | Excel/CSV 导入、字段映射、PII、upsert | `app_inspect_file` → dry-run → mapping → PII → 导入 → 核对 | 按需加载；简单应用不必加载 |

`miniapp-builder` 的 references 建议：

```
skills/miniapp-builder/
├── SKILL.md                 # 主流程、决策树、验收清单
└── references/
    ├── sdk.md               # octopApp 全量 API 与示例
    ├── data.md              # 字段类型、多表、reference、aggregate、行历史
    ├── security.md          # 沙箱红线、能力位、HTML 扫描、CSP
    └── publish.md           # 公开表、快照、IM 快速路径
```

### 3.4 `AGENTS.md` 应该保留什么

把 `AGENTS.md` 缩到 80–120 行，只保留「始终需要」的内容：

- 角色与语气（SOUL.md 已有部分）。
- **必须先加载 `miniapp-builder` 技能**再创建/修改 MiniApp。
- 六条硬红线：
  1. 不引入任何远程资源；不写 `fetch`/`XHR`/`WebSocket`/`sendBeacon`。
  2. 不在页面里写密钥、token、连接器凭据。
  3. 不直接写 `apps/<app_id>/` 下的文件，只用 `app_*` 工具。
  4. 删除应用、删列、清 PII 标记、公开分享前必须得到用户明确同意。
  5. 高风险能力（`ai.generate`、`game.launch`、`external.fetch`、`events.emit`、`schedules.manage`）只能由 owner 在详情页开启。
  6. 发布前必须走 `miniapp-security-review` 清单。
- 交付纪律：`app_open` 只在首个完整版本调用一次；后续编辑不再调用。
- IM 快速路径：只调一次 `app_share_link`，不 curl/截图/自托管。

### 3.5 不建议建哪些技能

- **一个工具一个技能**（20+ 个）：触发词重叠、维护成本高，工具说明放 `references/sdk.md` 或工具 description 即可。
- **模板技能**：模板是数据，不是流程；放 `app_templates/` 即可。
- **工作流/流程引擎技能**：MiniApp 不是流程引擎，编排归 `infra/cron` / `infra/events` / Teams。
- **通用「小程序使用」技能**：技能是给 Agent 的，不是给最终用户的；用户侧要靠产品 UI 和帮助文案。

### 3.6 实施顺序

1. 先写 `miniapp-builder` 主 SKILL + `references/security.md`，把现有 AGENTS.md 的内容迁过去。
2. 写 `miniapp-security-review`，先覆盖发布/分享/高风险能力三个场景。
3. 把 `AGENTS.md` 缩成红线 + 技能入口，验证简单任务不再加载全量手册。
4. 数据导入流程稳定后再拆 `miniapp-data-import`；不要一开始拆太细。
5. 用 `skill-manager` 的 `inspect` 校验 skill 包；跑现有 skills 测试。

---

## 4. 如何避免 Agent 误用或用户绕过安全限制

### 4.1 威胁模型

| 威胁 | 例子 | 主要防线 |
|---|---|---|
| **Agent 生成不安全 HTML** | 引 CDN 脚本、`fetch` 外部 API、iframe、`window.open`、`location.href` | 写入时扫描 + CSP + 宿主桥 |
| **Agent 过度声明能力** | 建应用时声明 `external.fetch` / `ai.generate` | `app_set_capabilities` 只允许低风险；高风险 owner 开启 |
| **Agent 破坏性操作** | 删列、删应用、清 PII 标记、整表覆盖导入 | 用户确认 + prepared operation + 审计 |
| **用户自己写 HTML / 让 Agent 加新功能** | 直接调用不存在的 API、嵌入外部脚本、读别的应用数据 | 关闭的 API 面 + 能力桥 + 服务端 ACL |
| **数据外泄** | 把行数据拼进 URL、图片、LLM prompt、webhook | CSP 无网络 + 能力位 + URL/目标白名单 + PII 脱敏 |
| **沙箱逃逸** | iframe、popup、Worker、WebRTC、导航离开 | opaque origin + wrapper + CSP + freeze + tripwire |
| **跨应用访问** | 伪造 `app_id` / table id / row id | 宿主绑定 app_id + 服务端 ACL/行 scope |
| **供应链** | 恶意模板、外部 service、Skill | 模板/服务审查 + owner 声明 + 最小权限 |

### 4.2 四层防线

| 层 | 机制 | 当前实现 | 仍需补 |
|---|---|---|---|
| **1. 生成时** | HTML 外链扫描、schema/行校验、大小/行数/表数限制、能力白名单、计划审批 | `models.py` 的 `find_external_refs`、`validate_html`、`validate_row`、`app_*` 工具限制 | 破坏性/公开操作的服务端确认绑定；应用目录写保护；发布前安全审查 |
| **2. 浏览器沙箱** | opaque origin、只 `allow-scripts`、双层 iframe、CSP、integrity freeze、导航 tripwire、CSP 遥测 | `dashboard/src/components/MiniAppFrame/wrapper.ts`、`appCsp.ts`、`sdk.ts`、`sandbox.py` | 保持现状；不要为 CDN/富媒体放宽 CSP |
| **3. 宿主桥** | 方法白名单、能力映射、`event.source` 校验、app_id 由宿主绑定、`openExternal` 确认、超时 | `bridge.ts`、`capabilities.ts`、`index.tsx` | 服务端 page-capability token；新增方法默认拒绝；拒绝事件审计 |
| **4. 服务端** | 用户认证、应用 ACL、行 scope、能力检查、PII 脱敏、限流、审计、公开页只读 | `api/routers/apps.py`、`public_apps.py`、`service.require_capability`、`limits.py` | 所有 page-facing 高风险端点统一 `require_capability`；发布/分享决策审计 |

### 4.3 现有实现盘点

- **沙箱**：`MiniAppFrame` 只给 `sandbox="allow-scripts"`；不透明源、无 JWT、无同源；wrapper 的 `frame-src 'none'` 阻止应用导航离开；第二次 load 触发 `escaped`，宿主卸载 frame。
- **CSP**：`default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'`。
- **运行时冻结**：`WEBRTC_FREEZE` 禁用 WebRTC、iframe/frame/object/embed、Worker/SharedWorker/WebTransport、`window.open`、ServiceWorker；拦截 `innerHTML`/`insertAdjacentHTML` 等插入 frame。
- **完整性检查**：SDK 每次调用前 `intact()`；发现 iframe/WebRTC 就上报 `sandbox_integrity`。
- **写入扫描**：`find_external_refs` 拒绝外链 script/link/img/media/iframe/object、CSS `@import`/`url()`、`fetch`/XHR/WebSocket/EventSource/sendBeacon、`location`/`window.open`/WebRTC、`meta refresh`、`javascript:`。
- **能力桥**：`bridge.ts` 只接受 `event.source === frame.contentWindow` 的消息；`checkMethodAllowed` 对未知方法默认拒绝；`capabilities.ts` 映射方法与能力位。
- **服务端**：`generate`、`service`、`events.emit` 都调用 `require_capability`；公开页只有 GET；PII 脱敏、限流、审计已接入。
- **遥测**：`POST /api/apps/{app_id}/security-events` 记录 CSP 违规与 sandbox integrity，按 app+user 限流，审计前去掉 query/fragment。

### 4.4 仍存在的缺口与建议

#### 缺口 1：应用 HTML 可以被通用文件工具直接改写

`app_write_html` 会调用 `validate_html`，但 `AppService.read_html()` 只是从工作区读取 `apps/<app_id>/app.html`，读取和发布时没有再校验。Agent 或用户用通用 `write_file`/shell 直接改这个文件，就会绕过 HTML 外链扫描；`app_share_link` 也会把未校验的 HTML 冻结进公开快照。

需要区分三类文件：

- **v2 应用**：schema 与行数据以 DB 为准（`_read_schema()` 在 `storage_version >= 2` 时直接读 DB），直接改 `schema.json` / `*.csv` 不会改变运行时数据；但 HTML 始终来自工作区，仍是主要绕过面。
- **v1 legacy 应用**：schema/CSV 仍是工作区权威，直接改会绕过 schema、PII、行校验与行历史。
- **manifest.json**：只是可读快照，不参与能力强制；改它不会提权，但会误导查看者。

**建议**：

1. 应用目录设为「服务所有」：`apps/<app_id>/**` 只允许 `app_*` 工具写，通用文件工具直接拒绝。
2. 读取/发布时再次 `validate_html`（v1 再加 schema 校验）；失败则拒绝加载/发布。
3. 给 app 文件加内容 hash；`app_share_link` 只发布通过校验的 hash。
4. 直接文件写入记审计，并告警。

#### 缺口 2：破坏性/公开操作依赖 prompt 确认（暂缓，已知缺口）

`app_delete` 有 HITL；但以下操作目前主要靠 Agent 的提示词自律：

- `app_alter_schema(drops=...)` 删列；
- `app_alter_schema(pii={...: None})` 清除 PII 标记；
- `app_import_rows(include_pii=True)` 导入敏感列；
- `app_set_table_visibility(public=True)`；
- `app_share_link` 公开分享；
- `app_set_capabilities` 修改页面能力。

**建议**：把这些操作纳入 prepared operation：

1. 先生成一份可审阅的影响清单（列名、行数、表、公开范围、PII、能力差异）。
2. 服务端保存 fingerprint，`ask_user` 绑定 `prepared_id`。
3. 执行时重新计算 fingerprint，不一致直接拒绝；确认只能消费一次。
4. 审计记录「用户批准了哪份具体操作」。

这比「工具描述里写要确认」可靠得多。Magic 的 `prepared_operation_confirmation.py` 是现成参考。

#### 缺口 3：能力位主要在前端桥，服务端只覆盖部分高风险端点（暂缓，已知缺口）

当前 `generate`、`service`、`events.emit` 有服务端 `require_capability`；数据 CRUD 依赖 ACL/行 scope，不依赖页面能力位（这是有意设计：Dashboard 和 Agent 工具不受页面能力限制）。

风险在于：如果 bridge 未来新增方法、或某个 page-facing 端点忘了 `require_capability`，页面可能通过合法端点做未声明的事。

**建议**：

1. 所有 page-facing 高风险端点统一 `require_capability`，并维护一张「端点 ↔ 能力」表。
2. 宿主为每个 iframe 签发短期 page-capability token（只含 app_id、允许的方法、过期时间），服务端在高风险端点校验；防止 bridge 漏洞或未来方法默认开放。
3. `bridge.onDenied` 上报考频事件，审计「页面尝试未声明能力」。
4. 新方法加入 `KNOWN_METHODS` 时必须同时加能力映射，否则默认拒绝。

#### 缺口 4：HTML 扫描是启发式，不是安全边界

`find_external_refs` 能挡住常见写法，但模型可以混淆（字符串拼接、`eval`、`Function`、编码）。不要把它当成沙箱的替代品。

**建议**：

- 保持 CSP/沙箱为真正的边界；扫描只用于「让 Agent 尽早改对」和审计。
- 可选增加 JS AST 静态分析（检测动态 `fetch`/`import`/`eval` 构造），但结论仍是「CSP 兜底」。
- 对 `eval`/`Function`/`atob` 等高风险模式给 warning，不阻断；阻断可能误伤正常代码。

#### 缺口 5：默认能力集偏宽

新应用默认包含 `data.read` / `data.write` / `asset.read` / `external.open` / `chat.compose` / `events.read`。对含 PII、要公开分享、或只做展示的应用，这个集合偏大。

**建议**：

- 展示型应用默认 `capabilities=[]`；`app_create` 不传 capabilities 时按场景建议，而不是一律安全集。
- 含 PII 或准备公开的应用，默认去掉 `external.open` / `chat.compose`，由 owner 显式开启。
- `openExternal` 确认框显示完整 URL；如果 URL 含长 query，提示用户可能携带数据。

#### 缺口 6：公开链接的发布前检查不够显式

`app_share_link` 会冻结 HTML + 公开表结构，PII 会脱敏；但 Agent 可能在未检查公开表/PII 的情况下直接发布。

**建议**：

- `app_share_link` 前强制 `miniapp-security-review`：列出公开表、行数、PII 列、能力位、外部 service、webhook。
- 服务端返回 `sharing_notice`，要求调用方把公开范围告诉用户；高风险（含 PII 或写能力）要求 owner 明确确认。
- 发布历史保留每次快照与审批记录。

#### 缺口 7：审计覆盖不完整

已有 CSP/integrity 事件、`app.generate`、公开访问审计；建议补：

- 页面能力拒绝事件；
- 能力位变更（谁把哪个能力打开/关闭）；
- 发布/分享决策（公开了哪些表、哪些 PII 列）；
- 直接文件写入/校验失败；
- Agent 数据写入的 source（agent vs page vs import）。

### 4.5 正确的新增功能路径

如果用户/Agent 想实现一个 MiniApp 当前不支持的功能，**不能靠改 HTML 绕过**。正确路径是：

1. 判断需要哪一种能力：数据读写、资产、外部服务、AI、事件、调度。
2. 在平台侧增加：服务端端点 + 能力位 + 宿主桥方法 + 前端 SDK + 权限检查 + 限流 + 审计 + 测试 + 文档。
3. 新能力必须走 `CAPABILITIES_VERSION`，老应用不会自动获得。
4. 高风险能力必须 owner 显式开启；页面只能请求已声明、已开启的能力。
5. 如果找不到现成能力，Agent 应停下来说明「这个功能需要平台支持」，而不是用 `fetch`、`openExternal`、`generate` 或直接写文件去模拟。

判断标准很简单：**页面能做的事 = bridge 暴露的方法 = 服务端有对应端点且做了能力检查**。三者缺一，功能就不成立；页面无法自己创造新的宿主 API，也无法直接访问网络或数据库。

### 4.6 Agent 规则 vs 安全边界

- **Agent 规则**（AGENTS.md / skill）负责：选对场景、用对工具、不引入外链、不在页面写密钥、破坏性操作先问、发布前审查。它降低「无意错误」。
- **安全边界**（沙箱 / 桥 / 服务端）负责：即使 Agent 被诱导、用户自己写 HTML、或有人试图绕过，也拿不到未授权的数据和能力。它阻止「有意逃逸」。

两者都要有，但不能互相替代。**不要把安全寄托在「模型会遵守提示词」上。**

---

## 5. 落地顺序

| 优先级 | 事项 | 产出 |
|---|---|---|
| **P0（已完成）** | 应用目录写保护（agent 文件工具）+ 读取/发布时二次校验（2026-11-17） | 堵住「直接改文件」这条最现实的绕过路径 |
| **P0（暂缓，已知缺口）** | 破坏性/公开操作 prepared operation；page-facing 高风险端点统一 `require_capability` | 暂缓期间靠 `app_delete` HITL、备份/恢复、`sharing_notice` 和前端桥白名单兜底；对外/PII/多用户前必须重新评估 |
| **P1** | 拆分 `miniapp-builder` + `miniapp-security-review`；AGENTS.md 缩成红线；发布前安全审查 | 降低上下文成本、提高生成质量 |
| **P1** | 公开分享前 PII/公开表/能力预检；`sharing_notice` 强制展示 | 降低数据外泄风险 |
| **P2** | page-capability token；JS 静态分析；能力拒绝审计；发布历史 | 防御纵深与可追溯 |
| **P2** | 匿名表单、页面附件上传、应用发起 Agent 任务 | 产品能力扩展（见 [MiniApp 与 Magic 的参考](./miniapp-magic-reference.md)） |

**如果只能先做一件**：做 **应用目录写保护 + 读取/发布二次校验**。它不改变产品交互，成本低，能直接堵住「Agent/用户绕过 `app_write_html` 直接写 HTML」这条路径；v2 的 schema/行数据已在 DB，不受文件直改影响。

> 2026-11-17 已落地 P0 第一条：`SecuritySettingsStore` 在全局策略和 agent 级覆盖之后都强制 `/apps/app_*/**` 写拒绝；`AppService.read_html(validate=True)` 在 `GET /api/apps/{id}/html`、公开页和服务端 `publish` 复验 HTML。剩余 prepared operation 与 page-facing capability 统一校验**未做**，已在 §0.1 标为「暂缓（已知缺口）」，不得按「已覆盖」处理。

---

## 6. 证据索引

| 主题 | 路径 |
|---|---|
| 应用模型 / 字段 / HTML 扫描 | `src/octop/infra/apps/models.py`（`reference`、`find_external_refs`、`validate_html`） |
| 应用服务 / 行历史 / reference / 聚合 | `src/octop/infra/apps/service.py:4268`、`:2478`、`:4199` |
| 聚合实现 | `src/octop/infra/apps/aggregate.py` |
| Agent 工具 | `src/octop/infra/apps/tools.py` |
| 能力位 | `src/octop/infra/apps/capabilities.py:32` |
| 限流 | `src/octop/infra/apps/limits.py` |
| 应用 API | `src/octop/api/routers/apps.py` |
| 公开页 API | `src/octop/api/routers/public_apps.py` |
| 服务端沙箱 wrapper | `src/octop/infra/apps/sandbox.py` |
| 前端沙箱 wrapper | `dashboard/src/components/MiniAppFrame/wrapper.ts` |
| CSP | `dashboard/src/components/MiniAppFrame/appCsp.ts` |
| 宿主桥 | `dashboard/src/components/MiniAppFrame/bridge.ts`、`capabilities.ts`、`index.tsx` |
| SDK | `dashboard/src/components/MiniAppFrame/sdk.ts` |
| light-apps 专家手册 | `src/octop/infra/agents/experts/library/light-apps/AGENTS.md` |
| light-apps 专家人设 | `src/octop/infra/agents/experts/library/light-apps/SOUL.md` |
