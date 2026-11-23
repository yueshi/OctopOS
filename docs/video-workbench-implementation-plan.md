# Octop 视频工作台落地方案（MiniApp + 外部扩展服务）

> 状态：**修订稿（2026-11-23 评审后）**  
> 日期：2026-11-23  
> 范围：企业版功能 / MiniApp 工作台 / 外部视频扩展服务 / 手动交付  
> 依据：当前分支 `feature/miniapp-ai-native`，schema v52；实现以代码与测试为准。  
> 修订说明：本稿按 2026-11-23 设计评审收口，分 V1（流水线验证）与 V2（团队工作台）两期。  
> **实施进度（2026-11-23）：V1-B 平台能力已落地**——extension 读、动态 CSP、50MB 媒体上传（4MB 分片会话）、媒体 Range、私网开关、前端 SDK、管理员配置 UI、`submission_unknown` 对账入口，以及 `video-workbench` 系统模板。  
> **评审补充见 §16：实现前阻断项、P0–P3 优先级和建议的最小 V1 验收。**  
> **本方案取代 `docs/ai-video-commerce-system.md` §3.2/§3.3/§5 Sprint 2 的 in-Octop `video_jobs` + `VideoPipelineWorker` 落点**；该文的业务、合规与归因结论仍然有效。

---

## 0. 结论摘要

**最终形态：一个 MiniApp = 一个团队视频工作台 + 一个外部扩展服务。**

- UI 用普通 `light_apps` MiniApp：保留应用列表、协作者、数据 Tab、行历史、Board/桌面/聊天入口、通知。
- 一个 MiniApp 内用 `runs` 表承载多条视频任务；多人共享应用，按 run owner 区分操作权限。
- MiniApp 新增 `external.write` 写服务能力：页面可以安全地提交任务、选首帧、取消/重试。
- 视频流水线、Job Worker、Asset、FFmpeg/ASR/VLM、内容安全、用量全部在扩展服务。
- Provider 首期支持 Volcengine Seedance/Seedream；MiniMax H3（视频）作为重点验证与备选通道；H3 image 暂不纳入。
- MVP 用签名 Extension Bundle + 管理员手动安装；工作台走系统模板；离线 License；不做市场、不做自动安装、不做在线计费。
- 市场就绪只保留**接口边界**（CatalogSource / InstallDriver / EntitlementProvider / UsageSink），MVP 不建市场表、不建自动安装；后续上市场再补实现。
- SQLite 可作为单机 MVP/试点；生产多 Worker/企业用 PostgreSQL；同一套代码双后端。
- 企业定位：单实例单企业，不做多租户 SaaS。

### 0.1 评审后的关键决策（2026-11-23）

1. **架构**：保留外部扩展服务；Octop 不跑视频流水线。分两期：V1 先跑通「外部服务 + 审片/查看」，V2 再加多人写工作台。
2. **权威**：扩展服务 DB 是视频域唯一权威；MiniApp `runs` 表只是投影。投影的系统列（status/asset/error/updated_at）不得由页面直接写，需列级 `write_policy`，或改为不投影、列表走读服务。
3. **回写**：复用 `light_app_ingress` + `app.import`，不新增第二套投递引擎；异步状态带 `status_seq` 单调守卫，终态复用 `notifications`。
4. **写服务**：`external.write` 默认关闭、owner/admin 开启；operations 只做薄幂等账本，不建第二套任务运行时；幂等键按 actor 作用域，确认走两阶段。
5. **媒体**：商品图复用现有 `asset.write`；参考视频走 Octop 流式代理，V1-B 50MB，后续再扩 200MB/续传；媒体端点支持 Range/206/HEAD，token 30 分钟；CSP 同时更新 `APP_CSP` 与 `WRAPPER_CSP`。
6. **复用**：用量写 `usage_log`（已有 `app_id`/`task_id`），通知复用 `notifications`，任务状态词表向 `light_app_tasks` 对齐；不建 `extension_usage_events`。
7. **范围**：MVP 不做 053 市场表、不做自动安装、不做多 Provider 自动路由、不做 MCP、不做公开媒体；工作台先用系统模板交付。
8. **Provider**：按「至少一次」设计，`submission_unknown` 人工对账；不要承诺 exactly-once。

---

## 1. 产品定位与目标

### 1.1 目标

1. 企业用户在一个 MiniApp 内完成“多人、多条视频”的生产协作。
2. 覆盖单镜头爆款克隆：素材上传 → 视频分析 → 商品档案 → 创意/文案 → 内容安全 → 首帧候选 → 图生视频 → 预览/下载。
3. 最大化保留 MiniApp 的操作体验：应用列表、协作者、数据 Tab、行历史、Board/桌面/聊天、通知、导出。
4. 支持企业私有化：单实例、手动安装、离线 License、数据留在客户侧。
5. 为云端应用市场预留接口边界（Bundle、安装状态机、授权、计量、目录），但 MVP 不建市场表与自动安装。

### 1.2 非目标（MVP 明确不做）

- 多租户 SaaS；每客户一个实例。
- 通用可视化工作流编辑器。
- 多镜头剪辑、时间轴、逐帧编辑。
- 自动配音、字幕、BGM。
- 公开写入 / 匿名填报。
- 云端市场、自动安装扩展服务、自动更新。
- 通用扩展注册/安装状态机（053 的七张市场表）；MVP 只做单个 Extension Bundle 的手动安装。
- 公开媒体 / 公开审片链接；MVP 公开链接不播扩展服务媒体。
- MCP 门面（V3 再做）。
- 在线支付、发票、订阅管理。
- 批量生产、多 Provider 自动路由、多视频并行（同一 run 内）。
- 完整广告法规知识库、复杂 RBAC/LDAP/SSO 新能力。

### 1.3 用户与角色

| 角色 | 说明 | 权限 |
|---|---|---|
| 管理员 | Octop admin | 安装/配置扩展服务、License、Provider、审计 |
| 应用 owner | MiniApp 创建者 | 全部任务、协作者、写服务配置 |
| 编辑者 | `edit_data` 协作者 | 新建任务、查看全部、编辑内容 |
| 查看者 | `view` 协作者 | 只读全部任务 |
| run owner | 任务创建人 | 确认创意、选首帧、取消/重试自己的 run |

---

## 2. 总体架构

```text
MiniApp「视频工作台」（light_apps）
  ├─ runs 表：一行一条视频任务
  ├─ 页面：任务列表 + 详情抽屉 + 新建向导
  ├─ octopApp.service("video")
  │    ├─ call()      只读：runs.get / runs.list / jobs.get / assets.get
  │    ├─ invoke()    写：runs.create / frames.select / video.submit / cancel
  │    ├─ upload()    宿主代传：参考视频/商品图
  │    └─ mediaUrl()  首帧/成片预览
  │
  └─ Octop 服务端
       ├─ /api/apps/{id}/services/{name}/call        （只读，现有）
       ├─ /api/apps/{id}/services/{name}/invoke      （写，新增）
       ├─ /api/apps/{id}/services/{name}/operations  （幂等/异步）
       ├─ /api/apps/{id}/services/{name}/upload      （宿主代传）
       └─ /api/apps/{id}/services/{name}/media/{asset}（Range + token）
              │
              ▼
       视频扩展服务（Docker Compose / 托管）
         ├─ REST /v1 + 可选 MCP
         ├─ run/job/asset/usage/review 表
         ├─ Worker + Provider 轮询
         ├─ FFmpeg/FFprobe + ASR + VLM/LLM
         ├─ 内容安全 + License
         └─ PostgreSQL 或 SQLite（同一套代码）
```

### 2.1 组件职责

| 组件 | 职责 | 不做什么 |
|---|---|---|
| MiniApp 工作台 | 任务列表、详情、表单、状态展示 | 不跑视频、不存大文件 |
| Octop 写服务代理 | 鉴权、能力位、白名单、幂等、审计、媒体代理 | 不写业务流水线 |
| 扩展服务 | WorkflowRun/Job/Asset/Provider/FFmpeg/Safety | 不依赖 Octop 进程内状态 |
| Bundle/Installer | 模板、服务包、License、安装文档 | MVP 不自动安装 |
| License/Usage | 功能开关、到期、席位、用量事件 | MVP 不接支付 |

### 2.2 边界原则

- 页面不直连扩展服务；扩展服务密钥不进沙箱。
- 扩展服务 DB 是视频域权威；MiniApp `runs` 表是投影与索引。
- Octop 不新增视频调度循环；扩展服务独立 Worker。
- **单一写入者**：扩展服务是唯一权威；Octop 只做策略、代理与投影。投影的系统列只允许系统来源写（列级 `write_policy=system`，或不做投影、改为读服务）。
- **单一事件平面**：状态回写复用 `light_app_ingress` / `app.import`；不新增第二套 webhook/投递引擎。
- **单一账本**：扩展 `video_usage` 只做 Provider 对账；平台用量写 `usage_log`；不建 `extension_usage_events`。
- `light_apps` 仍是应用壳；不把 Workflow/Usage/事件塞进 `light_app_rows`。

---

## 3. MiniApp 工作台

### 3.1 一个 MiniApp = 团队工作台

- 一个应用代表一个团队/业务空间的视频工作台，不是单个项目。
- 应用协作者即团队成员；最多 50 人。
- 任务用 `runs` 表承载；一个应用可有多条视频任务并发/排队。
- 如需项目级隔离，使用多个 MiniApp；MVP 不做应用内项目 ACL。

### 3.2 `runs` 表

| 列 | 类型 | 说明 |
|---|---|---|
| `run_id` | string | ULID；同时作为扩展服务 run_id；创建时可用作幂等键（按 actor 作用域） |
| `title` | string | 视频标题/商品名 |
| `status` | enum | 状态机字段（系统列） |
| `owner_name` | string | 展示快照；归属用行元数据 `row_owner_user_id` / `created_by` |
| `reference_asset_id` | string | 参考视频 Asset |
| `product_asset_id` | string | 商品图 Asset |
| `creative_summary` | text | 创意/文案摘要；完整内容在扩展服务 |
| `selected_frame_asset_id` | string | 选中的首帧（系统列） |
| `video_asset_id` | string | 成片（系统列） |
| `review_decision` | enum | V1 审片：`approve`/`reject`/`needs_review`（用户列） |
| `review_note` | text | 审片意见（用户列） |
| `error` | text | 最近错误摘要（系统列） |
| `created_at` / `updated_at` | datetime | 列表排序 |

约束：

- `row_scope=app`，所有协作者可见；「我的任务」直接用 `row_owner_user_id` / `created_by`，**不要另设 `owner_user_id` 列**（与行元数据重复，且可被页面伪造）。
- **状态/资产类是系统列**：`status`、`selected_frame_asset_id`、`video_asset_id`、`error`、`updated_at` 只允许扩展服务回写（`write_policy=system`）。MVP 若暂不引入列级策略，则不要把状态放进页面可写表，列表/详情改读 `runs.list` / `runs.get`。
- `rules_json` 只约束用户动作的状态边，**不能**作为扩展服务回写的权限依据。
- 行历史只记录阶段变化，不记录进度百分比；回写只更新系统列，不整行 replace，避免覆盖用户正在编辑的创意。
- 一条一行；20k 行/5MB 足够 MVP；成片/首帧/事件/用量不进表。
- 数据 Tab 直接展示 `runs` 表；行历史与 CSV 导出可用。

### 3.3 状态机

**run 状态**（业务视角，写入 `runs.status`）：

```text
draft → queued → analyzing → waiting_creative → generating_frames
      → waiting_frame → generating_video → done
      ↘ cancelled（queued/analyzing/generating_* 均可）
      ↘ failed（终态失败）
      ↘ blocked / needs_review（内容安全或人工拦截）
```

- `retry` 的语义必须二选一并在实现中固定：**同一 run 新建 attempt**（推荐，保留 run 历史）或复制新 run；文档与 UI 不得混用两种语义。
- `failed` 只在 run 无可用 job 或用户放弃时进入；单个 job 失败可重试，不直接把 run 标 failed。
- `waiting_creative` / `waiting_frame` / `blocked` 为人工节点。
- 权限：`runs.create` = `edit_data`；确认/选帧/取消/重试 = run owner 或 app owner/admin；查看媒体 = `view`。`confirmation_json` 用 `app_owner` / `run_owner`，不要写裸 `owner`。
- 扩展服务负责 CAS 动作锁（`expected_status` / `status_seq`）；Octop 只做前置校验，不信任页面。
- 进度百分比只在详情轮询；列表只显示阶段，不写百分比。

**job 状态**（执行视角，写入扩展服务 `video_jobs`）：

```text
queued → running → waiting_provider → succeeded
                              ↘ failed / cancelled / submission_unknown
```

- 一个 run 可以有多个 job（analyze / frames / video）；`run.status` 由 jobs 聚合，不与 job 状态一一对应。

### 3.4 页面结构

单文件 HTML，一个页面 + 抽屉：

1. 顶部：新建视频、筛选（全部/我的/进行中/待确认/已完成）、搜索；
2. 任务列表：状态 chip、owner、更新时间、缩略图；
3. 任务详情抽屉：阶段时间线、素材、创意、首帧候选、视频、错误；
4. 新建向导：商品信息 → 上传参考视频/商品图 → 创意 → 提交；
5. 数据 Tab：`runs` 表 + 行历史 + 导出。

约束：

- HTML ≤512KB；原生 JS/Preact；不塞重型图表库。
- 详情在同页，不跳路由。
- 超限再考虑 Extension Workbench 或拆分页面。

### 3.5 多人/多任务体验

- 所有人看到同一任务列表；默认“全部”，可切“我的”。
- 新建任务立即出现一行；扩展服务并发执行，列表显示排队/生成中。
- 同一 run 只允许一个动作；不同 run 可并行。
- 按钮置灰 + 服务端校验；防重复提交。
- 状态同步：同步动作（create/confirm/cancel）由 Octop 代理在调用成功后立即写投影；异步阶段由扩展服务经 `light_app_ingress` 回写，带 `status_seq`；旧事件不得回退状态。
- 列表读取投影，详情以 `runs.get` 为准；投影滞后时详情优先，列表可显示 `synced_at`。
- 完成/失败/待确认进 `notifications`（`kind=app_run`），点击打开 `/apps/{id}?tab=runs&run=...`。

---

## 4. 写服务：MiniApp 外部写能力

### 4.1 现有只读服务的边界

现状：

- `light_app_services` 只读；`call_service` 强制 `_require_read_only`。
- `external.fetch` 能力位；TTL 缓存；公开链接只读缓存。
- `app_add_service` 的 `allowed_tools` 是只读工具。
- 入站 ingress / 出站 Webhook 是事件，不是请求/响应。

结论：现有能力不能承载“提交任务/选帧/取消/上传”的写操作。

### 4.2 新增能力位

```text
external.fetch    # 只读：service().call()
external.write    # 写：service().invoke() / operation / upload
```

规则：

- `external.write` 默认关闭；
- 只有 Owner/Admin 能开启；
- 不进入 `LEGACY_CAPABILITIES`；
- 不进入 `NEW_APP_CAPABILITIES`；
- 不进入 `AGENT_SETTABLE_CAPABILITIES`；
- 页面没有该能力位时，`invoke` 一律拒绝；
- Server 侧 `require_capability` + `Action.SERVICE_INVOKE`（要求 `edit_data`）双重校验，页面置灰只是 UX；
- Agent 工具不得添加/调用写服务；`app_add_service` 只允许读工具；
- 媒体 token 的签发属于 `external.write`，公开链接不签发。

### 4.3 服务声明扩展

`light_app_services` 增加/复用：

```text
mode                 read | write
read_tools_json      [{"name":"runs.get","method":"GET","path":"/v1/runs/{run_id}","input_schema":{...}}]
write_tools_json     [{"name":"runs.create","method":"POST","path":"/v1/runs","input_schema":{...}}]
auth_mode            service_token（MVP 只支持这一种）
base_url             https://video.example.com
extension_id         octop.video
timeout_seconds      30
max_request_bytes    65536
max_response_bytes   5242880（复用现有 SERVICE_MAX_RESPONSE_BYTES）
idempotency_required 1
confirmation_json    {"video.submit": "run_owner", "cancel": "run_owner"}
service_token        write-only：API 接收后加密存 secrets；返回只给 token_configured
secret_ref           内部 secrets key；不返回页面
allow_private_network 0 | 1（admin-only；允许 http/私网 base_url，用于同机 Compose）
```

说明：

- `provider` 新增 `extension`；现有 `SERVICE_PROVIDERS` 只有 `connector`/`external`（`src/octop/infra/apps/service.py:447`），需要同步扩展枚举与执行器。
- `provider=extension` 时 `connector_instance_id` 存空字符串；`base_url` 必填。
- 每个 tool 必须带 `method`、`path` 和 `input_schema`；`path` 里的 `{name}` 占位符从 args 取值并 URL 编码，取走的参数不再进 body/query。Octop 在转发前校验参数，扩展服务再校验一次。
- `read_tools_json` 的名字同时写入 `allowed_tools_json`，供只读 `call()` 复用；`write_tools_json` 只用于 `invoke`。
- `public_live` 对写服务固定为 0；`cache_ttl_seconds` 固定为 0；两者在保存时校验，不能只靠文档。
- 10 个新列偏多；除 `mode`/`base_url`/`extension_id`/`secret_ref` 外，其余可收进一个受 Pydantic 校验的 `write_policy_json`，减少迁移面。
- `auth_mode=connector` 不在 MVP；不要为写服务复用连接器 OAuth。

### 4.4 操作表（薄幂等账本）

```text
light_app_service_operations
  op_id, service_id, app_id, tool, args_hash, idempotency_key,
  status (pending_confirmation/running/succeeded/failed/cancelled/expired/submission_unknown),
  confirmation_token_hash, confirmation_expires_at,
  result_json, error_code, error_message,
  actor_user_id, created_at, updated_at, expires_at
  UNIQUE(service_id, tool, actor_user_id, idempotency_key)
```

- 写调用必须带 `idempotency_key`；同 key 但 `args_hash` 不同 → 409，不返回旧结果。
- 重复调用返回同一 op/结果；`pending_confirmation` 不执行副作用。
- 它是**代理幂等账本**，不是任务运行时：不加租约、不做执行、不建第二套任务 UI；用户可见进度在 `runs` 投影。
- `result_json` 存幂等重放所需的小结果（如 run_id、job_id、asset_id），受 `max_response_bytes` 限制；大对象/媒体留在扩展服务，结果里只放引用并脱敏。
- 网络超时/扩展 5xx 不能只标 `failed`：可能已执行，记为 `submission_unknown`，由扩展按同一 idempotency key 对账；不要自动重提。V1-B 提供 `POST .../operations/{op_id}/resolve`：提交人、app owner 或 admin 在任务列表核对后标记 `succeeded/failed/cancelled`。
- 过期清理：`expires_at` + 定时清理；保留期按审计要求配置。

### 4.5 API 与 SDK

API：

```text
POST /api/apps/{id}/services/{name}/invoke
GET  /api/apps/{id}/services/{name}/operations/{op_id}
POST /api/apps/{id}/services/{name}/operations/{op_id}/resolve  # unknown 对账
POST /api/apps/{id}/services/{name}/upload          # 50 MB 单次，流式代理
POST /api/apps/{id}/services/{name}/upload-sessions # 4 MB 分片：start
PUT  /api/apps/{id}/services/{name}/upload-sessions/{upload_id}/chunks/{index}
POST /api/apps/{id}/services/{name}/upload-sessions/{upload_id}/finish
POST /api/apps/{id}/services/{name}/media-token     # 短时 token + 同源 URL
GET  /api/apps/{id}/services/{name}/media/{asset_id} # Range/206/HEAD；JWT 豁免
```

- `invoke` 与 `upload` 必须带 `Idempotency-Key`；Octop 转发时附带
  `X-Octop-Actor-Id` / `X-Octop-App-Id` / `X-Octop-Tenant`。
- `operations` 是读操作，用 `GET`；支持 `?wait=` 长轮询可选。
- 媒体端点支持 `HEAD`、`Range`、`206`。

SDK：

```js
octopApp.service("video").call("runs.get", { run_id })
octopApp.service("video").invoke("runs.create", { ... }, { idempotencyKey })
octopApp.service("video").operation(opId)
octopApp.service("video").upload(file, { purpose: "reference_video", onProgress })
octopApp.service("video").mediaUrl(assetId)
```

- SDK 默认生成 ULID idempotency key，重试时复用；`operation` 带指数退避；`mediaUrl` 返回可在过期前刷新的 URL。

### 4.6 确认与审计

- 高风险工具（`video.submit`、`cancel`、`frames.select`）在 `confirmation_json` 标记。
- 第一次 invoke 只创建 `pending_confirmation` 的 operation（不执行），返回绑定 `actor + args_hash + TTL` 的短时令牌。
- 确认时用 CAS 消费令牌（`status='pending_confirmation' AND actor=? AND args_hash=?`），再执行；重复确认返回同一结果。
- 令牌过期、actor 不匹配、args 变化、op 已执行 → 409/410，不重新执行。
- 每次写记录 audit：actor、service、tool、args_hash、结果、op_id；扩展服务也记录自己的 audit，两边用 op_id 关联。
- 不记录 secret、完整 PII 或 provider 原始错误；`error_message` 只存脱敏摘要。

### 4.7 安全边界

| 风险 | 措施 |
|---|---|
| 页面调任意 URL | 只允许声明里的 `base_url` |
| SSRF | HTTPS + 禁止重定向 + 私网/IP 校验；默认拒绝 loopback/LAN，仅显式 per-install 管理员开关可放行 |
| 越权 | `external.write` 默认关闭；Owner/Admin 开启 |
| 重复计费 | 强制 idempotency_key + 唯一索引 |
| 大请求/响应 | max_request_bytes / max_response_bytes / timeout |
| 滥用 | 每服务/用户/分钟限流；并发上限 |
| 公开面 | 公开链接禁止 invoke |
| 恢复 | 备份不存 secret；恢复后写服务 disabled |
| Agent 提权 | Agent 不能设置 external.write |

---

## 5. 媒体与上传

### 5.1 上传

- 商品图继续走现有 `octopApp.upload()`（≤5MB 图片，`asset.write`）；参考视频走新的 `service.upload()`，避免把大文件塞进 iframe postMessage。
- **MVP 决策：Octop 流式代理到扩展服务**，不由页面直传扩展存储，也不返回 presigned URL；保持「页面不直连扩展服务」的边界。
- 流程：Octop 边收边转 → 扩展服务 MIME/FFprobe/SHA-256 → Asset 登记 → 返回 `asset_id`；Octop 只做大小/魔数/ACL/配额检查，不引入 FFmpeg。
- 限制：V1-B 50MB；SDK 用 4MB 分片会话（start → chunks → finish），宿主写临时文件后流式转扩展；单次 `/upload` 保留给小文件/外部客户端。停止后重试同一分片会因顺序校验失败，需重新开始会话。
- 扩展服务资产端也必须支持 `Content-Length`、超时与失败清理；未使用的草稿资产按 TTL 清理。
- App assets 仍只支持 ≤5MB 图片，用于封面/小图。

### 5.2 视频播放

```text
GET /api/apps/{id}/services/{name}/media/{asset_id}?token=...
```

- 同源代理；支持 `Range` / `206` / `Content-Range` / `HEAD` / `416`，流式转发，不整文件进内存。
- token 绑定 user + app + asset；**30–60 分钟**有效（5–10 分钟会在长视频播放/拖动中过期）；过期前由 SDK 刷新。
- 服务端校验 app ACL + 扩展服务授权；`Cache-Control: private, no-store`；日志脱敏；`Referrer-Policy: no-referrer`。
- 公开链接默认不播扩展服务媒体；需要时另做只读审片链接（后续）。
- 下载走同一端点加 `Content-Disposition: attachment`，不另开无鉴权下载。

### 5.3 CSP

- 只对声明了 media 的应用注入：
  - `img-src data: blob: https://<host>/api/apps/{id}/services/{name}/media/`
  - `media-src data: blob: https://<host>/api/apps/{id}/services/{name}/media/`
- 同时更新 `APP_CSP` 和 `WRAPPER_CSP`：wrapper 会继承 inner 的策略，只改一个视频仍然播不了（`dashboard/src/components/MiniAppFrame/appCsp.ts:22-35`）。
- 不使用通配 `https:`；
- 无 media 服务的应用保持现有 `APP_CSP` 不变；
- iframe 是 opaque origin，`'self'` 不匹配，必须写完整 origin；
- `connect-src 'none'` 保持不变：页面只能用 `<video src>` / `<img src>`，不能 fetch 媒体。

---

## 6. 扩展服务

### 6.1 职责

- 视频域权威：runs/jobs/assets/usage/reviews。
- Provider 调用：Volcengine Seedance/Seedream、MiniMax H3（视频）等；H3 image 暂不接入。
- FFmpeg/FFprobe、ASR、VLM/LLM。
- 内容安全、审计、计量。
- Worker 并发、重试、取消、租约、`submission_unknown`。

### 6.2 数据表

```text
video_runs        app_id, run_id, status, status_seq, owner_user_id, input_json,
                  result_json, created_at, updated_at
video_jobs        job_id, run_id, kind, status, provider, model, provider_task_id,
                  attempts, lease_until, error, idempotency_key, created_at, updated_at
video_assets      asset_id, app_id, kind, storage_key, sha256, mime, size,
                  metadata_json, created_at, purged_at
video_usage       usage_id, usage_event_id, run_id, user_id, provider, model, kind,
                  quantity, cost, price_version, currency, occurred_at
video_reviews     review_id, run_id, stage, status, findings_json, reviewer,
                  model, prompt_version, input_hash, created_at
```

- `video_usage` 是 Provider 对账账本；平台账本写 `usage_log`（见 §7.4），不要双写两份业务账。
- `video_assets` 按 `sha256` 去重；未使用的草稿资产按 TTL 清理；删除 run/app 时走归档/清理流程。

### 6.3 API

```text
GET  /v1/runs
POST /v1/runs
GET  /v1/runs/{run_id}
POST /v1/runs/{run_id}/confirm
POST /v1/runs/{run_id}/select-frame
POST /v1/runs/{run_id}/video
GET  /v1/jobs/{job_id}
POST /v1/jobs/{job_id}/cancel
POST /v1/assets/stream        # MVP：Octop 流式代理
POST /v1/assets/upload-url    # 后续：直传对象存储，需单独安全评审
POST /v1/assets/complete
GET  /v1/usage
```

- `/v1` 版本化；service token + tenant；`Idempotency-Key`；`X-Octop-Actor-Id`；OpenAPI。
- 状态回写经 Octop app-owned ingress（HMAC），不新增第二套 webhook 投递；可选 MCP 门面（V3）。
- 资产端点必须支持 `Range`，并与 Octop 媒体代理的 206/HEAD 契约一致。

### 6.4 Job Worker

- 状态：`queued/running/waiting_provider/succeeded/failed/cancelled/submission_unknown`。
- 提交前写幂等键；提交后立即持久化 `provider_task_id`。
- 崩溃恢复：先对账，再决定重提；避免重复计费。
- 租约/心跳/超时回收；重试退避；取消。
- 并发：每用户、全局、每 Provider。
- SQLite：单 Worker + 条件 UPDATE 租约；PG：`FOR UPDATE SKIP LOCKED`。

### 6.5 视频流水线

1. 参考视频：FFmpeg 抽 3–5 帧 + ASR 文本 + VLM 摘要；
2. 商品档案：VLM 预填 + 用户确认；
3. 创意/文案：LLM 生成 1–2 个方向 + 可编辑；
4. 内容安全：三道闸——文本（创意/文案）、图片（首帧候选）、视频（交付前）；Pass/Warning/Blocked/Uncertain；失败/超时/解析失败不得默认 Pass；Warning 进入 `needs_review`；
5. 首帧：2 张候选 + 重生成（Seedream 或 MiniMax image-01）；
6. 图生视频：单 Provider、单条、手动重试（Seedance 或 MiniMax H3 / H3-Max）；
7. 交付：FFprobe 校验、预览、下载、AIGC 标识（显式 + 隐式）、合规复核。

### 6.6 内容安全

- 独立 `ContentSafetyService`；
- 三道闸对应三个 stage，`video_reviews.stage` 记录 text/image/video；
- 版本化 Prompt；结构化 JSON；
- `Pass/Warning/Blocked/Uncertain`；
- Warning / Uncertain 进入 `needs_review`，不直接放行；
- 调用失败/超时/解析失败不得默认 Pass；
- 审核记录：模型、Prompt 版本、输入摘要、结论、时间；
- 审核角色：app owner/admin 可放行 Warning，run owner 不能自行放行；
- 企业合规：AIGC 标识、广告法、肖像/素材授权、保留期。
- AIGC 显式标识按 `docs/ai-video-commerce-system.md:35`：视频文字高度 ≥ 最短边 5%、持续 ≥2s；隐式标识写元数据；在 FFmpeg 步骤执行，QA 验证。

### 6.7 数据库

- SQLite：单机、单 Worker、本地存储；适合试点。
- PostgreSQL：生产、多 Worker、对象存储。
- 同一套 repo/迁移；Job claim 抽象。
- **不要**共享 Octop 控制面 SQLite 文件。

### 6.8 部署

- Docker Compose（MVP）；systemd/Helm 后续。
- API + Worker 分离；Provider Key 加密配置。
- 对象存储可选；本地卷用于试点。
- 健康检查：DB、存储、Provider、Worker 心跳；Provider 未配置时告警不阻断启动。
- 回调可达性：MVP 限定扩展服务与 Octop 同机/同网；托管阶段要求 Octop 公网可达，或提供 outbound-only 轮询。
- 离线安装：镜像 tar + checksums 一并交付，不能只给 `images.txt`。
- 反向代理：上传体量/超时（`client_max_body_size`、`proxy_read_timeout`）写进安装文档。

### 6.9 Provider 与模型路由（MiniMax H3 视频）

**首期 Provider 组合**：

| 环节 | 首选 | 重点验证 / 备选 |
|---|---|---|
| 视频生成 | Volcengine Seedance（2.5 / 2.0） | MiniMax **H3 / H3-Max** |
| 首帧/图片生成 | Volcengine Seedream | MiniMax **image-01 / image-01-live**（现有） |
| 视频理解 VLM | 豆包 Seed 多模态 | MiniMax 多模态 |
| 创意/文案 LLM | 豆包 Seed 文本 | MiniMax 文本 |

**范围说明**：

- 本期只新增/验证 **MiniMax H3 视频**；**暂不接入 H3-Image**。
- 首帧/图片继续使用 Seedream；MiniMax 侧保留现有的 `image-01` / `image-01-live`，不新增 H3-Image preset、适配器或配置。
- H3-Image 后续如需支持，需单独评估 `octop-harness` 适配器分支、`/v1/images/generations` / `/v1/images/edits` 端点、非幂等重试策略，不在本期范围。

**当前代码状态**：

- MiniMax 视频模型 `MiniMax-H3` / `MiniMax-H3-Max` 已在 `src/octop/infra/agents/settings/media_generation.py:106` 的 preset 中；
- MiniMax 图片模型当前 preset 为 `image-01` / `image-01-live`（`media_generation.py:105`）；
- `MiniMaxMediaProvider` 支持图片参考、视频首帧/尾帧/参考图；**`video_audio` 只是能力声明，提交 body 没有 `generate_audio`，音频不会生效**；**MiniMax 没有实现取消端点，取消只改本地 job 状态**（`.venv/.../octop_harness/media/providers/minimax.py`、`media/base.py:184-186`）。

**模型路由规则**：

- 每次 run/job 记录 `provider_id` + `model` + `provider_task_id`；
- env 只做默认值；支持 per-run/per-job 覆盖，切换 Provider 不需要改业务代码；
- MVP 不做自动 fallback；失败后由用户手动重试/切换 Provider；
- 提交前校验 Provider 能力：`image_references` / `video_first_frame` / `video_last_frame` / `video_references` / `video_audio`；**不能只看 flags，要按 adapter 实际约束校验**；
- MiniMax H3 已知约束（适配器已实现）：
  - H3 不暴露 seed；
  - 参考图不能与首帧/尾帧混用；
  - H3-Max 时长需 ≥5s；
  - H3-Max 仅支持 480p / 768p，不支持 1080p/2K；
  - 取消是本地状态，Provider 可能继续计费，UI 必须提示；
- 媒体 job 按「至少一次」设计：提交前持久化意图，提交后立即落 `provider_task_id`；崩溃恢复先对账，拿不到 task id 进入 `submission_unknown` 人工处理，**不自动重提，不承诺 exactly-once**；
- 所有模型调用写入 `video_usage`，记录 provider/model/图片张数/视频秒数/耗时/错误；平台账本同步写 `usage_log`，补 `price_version`/`currency`。

**配置示例**：

```ini
MEDIA_PROVIDER=volcengine|minimax
VIDEO_MODEL=doubao-seedance-2-5-260628|MiniMax-H3|MiniMax-H3-Max
IMAGE_MODEL=doubao-seedream-5-0-260128|image-01|image-01-live
```

**验收**：

- 用 MiniMax H3 生成一条带首帧的视频；
- 首帧用 Seedream 或 MiniMax image-01 生成 2 张候选；
- Provider probe 通过；
- 错误码/重试/用量记录正确；
- 切换 Provider 不需要改业务代码，只改 run/job 的 provider/model。

---

## 7. 权限、配额与商业

### 7.1 权限

- 应用 ACL：owner/admin/edit_data/view。
- run owner：确认、选帧、取消、重试。
- 扩展服务二次校验，不信任前端。
- 管理员覆盖操作审计。
- 项目级隔离：MVP 不支持应用内项目 ACL；需要隔离就拆 MiniApp。

### 7.2 配额与并发

```ini
MAX_ACTIVE_RUNS_PER_USER=2
MAX_VIDEO_JOBS_GLOBAL=2..4
MAX_IMAGE_JOBS_GLOBAL=4..8
MAX_JOBS_PER_PROVIDER=2
DAILY_VIDEO_SECONDS_PER_USER=...
DAILY_BUDGET_PER_USER=...
```

- 队列公平调度；显示排队位置；
- 欠费/超配额 fail closed；
- 预算硬闸门在提交视频前；
- 默认值按 Provider 配额校准：`MAX_VIDEO_JOBS_GLOBAL=2..4` 在 5 人团队会明显排队，演示/试点前调大并在 UI 展示队列位置。

### 7.3 License / Entitlement

- MVP：离线签名 License（客户/版本/到期/功能/席位）。
- 功能键：`video.workbench`、`video.generate`（`video.batch` 留到后续，不在 MVP）。
- **信任锚**：发布者公钥由 Octop 发行版或独立渠道 pin；Bundle 只带 `public_key_id`，不能把 `public_key.pem` 放进被验的 Bundle 里自签自验。
- **验签范围**：对 `SHA256SUMS` 或去掉 signature 字段的 canonical JSON 签名；规则写进 Bundle 文档，避免不同实现无法互验。
- **执行点**：扩展服务校验 License（它持有 Provider Key）；Octop 只展示/缓存状态，不单独放行。
- 过期：禁止新任务，允许查看/下载已有结果；在途任务允许完成。
- 托管阶段：订阅 + entitlement token + credits。

### 7.4 用量

- 扩展 `video_usage` 只做 Provider 对账；平台唯一账本写 `usage_log`（已有 `app_id`/`task_id`），补 `resource_type=video_seconds/image`、`price_version`、`currency`。
- 不建 `extension_usage_events`；如后续需要市场计费，再做一层幂等导入，键为 `usage_event_id`。
- MVP 只记录不收费；市场阶段接计费。

### 7.5 收费建议

- 自托管：年 License + 客户自带 Provider Key；
- 托管：订阅 + 包含 credits + 超额；
- 不做“一次买断无限生成”。

---

## 8. Bundle 与手动安装

### 8.1 Extension Bundle 结构

> 命名：本节的 Bundle 是 **Extension Bundle**，与 MiniApp Bundle v2（应用导出/导入）不是同一物，不要混用。

```text
octop-video-mvp-v1.0.0/
  MANIFEST.json
  SHA256SUMS
  bundle.sig
  workbench/           # 可选：MiniApp 模板源文件（MVP 由 system template 发布，不直接复制）
  extension/           # docker-compose.yml + .env.example + migrations + images.txt
  license/             # license.json + public_key_id（公钥不随包）
  docs/                # INSTALL/CONFIG/UPGRADE/UNINSTALL/SECURITY
  scripts/             # healthcheck.sh / smoke_test.sh
```

`MANIFEST.json` 至少包含：

```json
{
  "bundle_format": 1,
  "extension_id": "octop.video",
  "version": "1.0.0",
  "publisher": {"id": "octop", "name": "Octop", "public_key_id": "octop-2026"},
  "octop_min_version": "1.4.0",
  "api_version": "v1",
  "workbench": {"entry": "workbench/app.html", "manifest": "workbench/manifest.json"},
  "service": {"modes": ["manual", "docker-compose", "hosted"], "health": "/healthz"},
  "entitlement": {"license_schema": 1, "features": ["video.workbench", "video.generate"]},
  "signature": {"alg": "ed25519", "sig": "..."}
}
```

签名与信任：

- `license/public_key.pem` 不能作为信任锚；发布者公钥由 Octop 发行版或独立渠道提供，Bundle 只带 `public_key_id`。
- `bundle.sig` 的签名范围必须写清（推荐对 `SHA256SUMS` 签名；manifest 用 canonical JSON），否则不同实现无法互验。
- 验签失败、key_id 未知、`octop_min_version` 不满足 → 拒绝安装，不做降级。

### 8.2 安装步骤（管理员手动）

1. 校验签名/checksum；
2. 安装扩展服务：复制目录 → 配置 `.env` → 迁移 → `docker compose up -d`；
3. `healthcheck.sh` 检查 DB/存储/Provider/Worker；
4. 安装工作台：MVP 将 `workbench/` 作为 **system template** 随 Octop 发布（或走受支持的模板导入 API）；不要直接复制文件绕过 `validate_html` 与模板能力授权。跨主机模板分发（Bundle v2）留到 V1 之后；
5. 创建“视频工作台” MiniApp；
6. 在应用详情「服务」面板配置 `provider=extension`：base_url、mode、service token、读写工具 JSON、`allow_private_network`；也可由安装脚本调用 `PUT /api/apps/{id}/services/{name}`；
7. 开启 `external.write` 能力；
8. `smoke_test.sh` 跑通“创建 → 上传 → 分析 → 首帧 → 视频 → 下载”。

### 8.3 配置

```ini
DATABASE_URL=sqlite:///data/video.sqlite | postgresql://...
STORAGE_BACKEND=local|s3|cos
OCTOP_BASE_URL=https://octop.example.com
OCTOP_SERVICE_TOKEN=...          # Octop -> 扩展
OCTOP_CALLBACK_SECRET=...        # 扩展 -> Octop ingress HMAC，独立密钥
LICENSE_FILE=/opt/octop-video/license/license.json
PROVIDER_VOLCENGINE_API_KEY=...
PROVIDER_MINIMAX_API_KEY=...
MEDIA_PROVIDER=volcengine|minimax
VIDEO_MODEL=doubao-seedance-2-5-260628|MiniMax-H3|MiniMax-H3-Max
IMAGE_MODEL=doubao-seedream-5-0-260128|image-01|image-01-live
MAX_UPLOAD_MB=50        # V1-B 单次 POST；200MB/500MB 需分片/续传
WORKER_CONCURRENCY=1   # SQLite 固定 1
```

### 8.4 升级/回滚/卸载

- 升级：停 Worker → 备份 DB/存储 → 验签新 Bundle → 迁移 → 启动 → 健康检查 → 失败回滚。
- 卸载：停服务 → 吊销 token → 导出/删除数据 → 删模板/应用 → 保留审计。
- 全部手动、可审计；不做自动更新。

### 8.5 安装验收

- [ ] `healthcheck.sh` 通过；
- [ ] `smoke_test.sh` 通过；
- [ ] 应用模板可创建；
- [ ] （V2）`external.write` 开启后 invoke 成功；
- [ ] （V2）未开启时 403；
- [ ] 视频可上传/播放/下载；
- [ ] License 过期禁止新任务。

---

## 9. 市场就绪路径

### 9.1 四个 Provider 接口

| 接口 | MVP | 市场阶段 |
|---|---|---|
| `CatalogSource` | 无（MVP 不做市场目录） | 云端市场 API |
| `InstallDriver` | 人工安装（文档 + 脚本） | 托管开通 / Docker / systemd |
| `EntitlementProvider` | 离线 License（扩展服务自校验） | 市场订阅/credits |
| `UsageSink` | `usage_log`（Octop 唯一账本） | 市场计费 |

### 9.2 通用扩展表

```text
extensions              extension_id, publisher_id, name, icon, status, latest_version
extension_versions      extension_id, version, bundle_digest, manifest_json, signature
extension_installs      install_id, extension_id, version, install_mode, service_url,
                        token_ref, status, config_json
extension_entitlements  extension_id, plan, features_json, expires_at, license_blob
extension_usage_events  event_id, extension_id, install_id, user_id, kind, quantity
extension_audit         extension_id, install_id, action, actor, payload_digest, ts
extension_service_tokens token_id, extension_id, install_id, scopes, tenant_id
```

**MVP 不建这些表。** 下列是 V1/V2 之后的市场就绪设计，等第二个扩展或真实市场需求再建；MVP 只用 `light_app_services` + `secrets` 保存一个扩展的 URL/token，安装状态放 Bundle 的 `install.json` 或一张极小的 `extension_installs`。`extension_service_tokens.tenant_id` 在核心库没有 tenant 维度，等托管阶段再引入。

### 9.3 阶段

> 本节 M0–M4 是**市场成熟阶段**，与 §11 的实施分期 V1–V3 不是同一套编号。

| 阶段 | 分发 | 安装 | 授权 |
|---|---|---|---|
| M0 MVP | 本地 Bundle | 人工 | 离线 License |
| M1 远程目录 | 远程签名 `catalog.json` | 人工 | 离线 License |
| M2 托管服务 | 远程 Bundle | 自动开通 URL/token | 市场 entitlement |
| M3 自托管市场 | 远程 Bundle | 管理员批准后半自动 | 市场 entitlement |
| M4 生态 | 多扩展目录 | 多 Driver | 订阅/发票/分成 |

### 9.4 避免返工

- 通用 `extensions` 表、`/api/apps/{id}/services` 路径、`external.*` 能力；
- Bundle 从第一天带 manifest/签名/版本/兼容性/权限/服务描述；
- 安装状态机落库；
- License 只通过 `EntitlementProvider` 读取；
- 用量事件从第一天记录；
- 工作台 HTML 由系统模板下发；跨主机分发等 Bundle v2；
- 服务 URL/token 存 `extension_installs` + `secrets`；
- 扩展服务契约 `/v1`；
- 数据不写 `light_apps` 权威层。

---

## 10. 数据模型与迁移

### 10.1 Octop 核心

- **不要在文档里写死 052/053。** 当前 `051` 只在 feature 分支、未进 `main`/CHANGELOG；按 AGENTS.md §7，未发布 schema 应折叠进当时未发布的 `00N`，并与 `docs/miniapp-ai-native-plan.md:493` 的 S1 预算迁移（052+）协调。
- MVP 只加：
  - `light_app_services` 的写服务列（或一个受 Pydantic 校验的 `write_policy_json`）：`mode`、`base_url`、`extension_id`、`secret_ref`、`write_tools_json`、`timeout_seconds`、`max_request_bytes`、`max_response_bytes`、`idempotency_required`、`confirmation_json`。
  - 新表：`light_app_service_operations`。
  - 如果选「列级写策略」：app schema（`schema.json`，由 `infra/apps/models.py` 的 `Column` 解析）增加 `write_policy`（`page|system`），并在 insert/update/import/Agent 工具路径执行。
- 053 的七张市场表不进 MVP；V1/V2 需要时再建。
- 双份 SQL，PG 补齐 identity/索引；更新 `tests/unit/db/test_db_pool.py` 的版本断言（以实现时的当前版本为准）。

### 10.2 扩展服务表

- 见 §6.2；独立 DB/文件，不与 Octop 控制面共用。
- 资产存本地卷/对象存储；DB 只存元数据。
- SQLite/PG 可移植：TEXT ID、INTEGER 时间戳、JSON TEXT；Job claim 抽象。

### 10.3 兼容

- 现有 `light_app_services` 行 `mode='read'`，行为不变。
- 现有 `external.fetch`、公开链接、缓存、备份语义不变。
- 新能力位不进 legacy/default/agent-settable。
- **恢复后写服务 disabled，Owner 重新开启**：单应用归档恢复已有禁用外部集成的逻辑（`service.py:6221-6328`）；系统级整库恢复目前没有这个钩子，需要在全局恢复路径补上并加测试。
- 注意：Octop 系统级备份是整库快照，包含 `secrets` 表（`system_archive.py:265-269`）；「备份不存 secret」只适用于 Extension Bundle/扩展服务侧，不要写成对 Octop 系统备份的承诺。

---

## 11. 实施计划

### V1：流水线验证（约 6–8 周）

- 扩展服务骨架：run/job/asset + 一个图片 + 一个视频 happy path（Seedance/Seedream）；
- MiniMax H3 视频接入与 probe；H3 image 不纳入；
- Worker 租约、重试、取消、`submission_unknown`；`video_usage` 与 `usage_log` 对齐；
- 参考视频分析、商品档案、创意/文案、内容安全三道闸、首帧候选、图生视频、预览/下载；
- 平台（已落地）：extension 只读 `call()`、动态 CSP、50MB 流式上传、媒体 Range/206/HEAD、私网开关、前端 `service().invoke/operation/upload/mediaUrl`；
- MiniApp 审片台（V1-B）：读 `runs.list` / `runs.get`；只开 `runs.create`、`review.decide` 两个窄写；**不做 `runs` 系统列投影**；
- 审片决定直接 `invoke("review.decide")`，不依赖 app.import/ingress；
- 系统模板 `video-workbench` 随 Octop 发布；服务声明由安装脚本配置（`video` 服务）；
- Extension Bundle：Docker Compose + 手动安装 + healthcheck/smoke；不做市场表。

**验收**：一条带首帧的视频；首帧 2 张候选；Provider probe 通过；错误码/重试/用量正确；页面能创建 run、上传/播放/下载、提交审片决定；未授权 403；`submission_unknown` 可对账。

### V2：团队工作台（约 4–6 周）

> 进度（2026-11-23）：V1-B 平台已落地——schema v52、`external.write`、`provider=extension`、`invoke` + operations 薄账本、actor 级幂等/两阶段确认、extension 只读 `call()`、`submission_unknown`、动态 CSP、50MB 上传、媒体 Range、私网开关、前端 SDK 与 `video-workbench` 模板。**V2 的 `runs` 投影、完整工作台、License/交付层仍暂停**，待 V1 验证后再排。

- 平台：`provider=extension` + `external.write` + 薄 operations + actor/idempotency + 列级 write policy（或状态不投影）；
- 工作台：`runs` 投影、新建向导、详情抽屉、确认/选帧/取消/重试、通知、数据 Tab；
- 回写：`light_app_ingress` + `app.import` upsert + `status_seq`；同步动作由代理写投影；
- 交付：系统模板、离线 License、手动安装、备份/恢复禁用写服务。

**验收**：5 人一个 MiniApp、6 条任务；并发幂等；乱序回调不回退；token 过期可续播；未授权 403；恢复后写服务 disabled。

### V3：商业化与市场（后续，3–5 周+）

- 配额/预算/用量报表；
- License 管理、到期/宽限；
- 管理页：Provider、配额、审计、失败任务；
- 备份/恢复/升级/回滚演练；
- 远程目录/更新提示、托管扩展、自动安装、支付/发票；
- MCP 门面/更多扩展。

> 工期为粗略估算，需按团队规模与 Provider 联调时间校准；V1 与 V2 可部分并行，但 V2 的 `external.write` 不应阻塞 V1 的视频验证。

---

## 12. 测试策略

### 12.1 现有 MiniApp 回归

- `uv run pytest tests/unit/apps tests/integration/test_apps_api.py -q`
- `uv run pytest tests/integration/test_miniapp_sandbox.py -q`
- 前端：`capabilities.test.ts`、`bridge.test.ts`、`appCsp.test.ts`
- 手工：创建应用 → 写行 → 发布公开链接 → Board → 系统应用 → 聊天卡片。

### 12.2 新增测试

- 写服务：能力位拒绝、白名单、幂等重放、确认令牌、audit、公开链接禁写、恢复 disabled。
- 并发/幂等：同一 key 并发、同 key 不同 args → 409、确认令牌重放/过期/错 actor、跨用户 key 冲突。
- 回写：乱序/重复回调、`status_seq` 回退、投影 CAS 冲突、终态通知、行历史来源。
- 媒体：上传 50MB/超限/中断、Range 206/416/HEAD、token 过期续播、CSP 放行、跨应用 403。
- 多 run：并发创建、owner 权限、状态同步、重复提交。
- Job：重启恢复、`submission_unknown`、取消、超时、Provider 429/5xx。
- 扩展不可用：超时、5xx、扩展停机、恢复后 invoke 403。
- 契约：fake extension + OpenAPI/tool schema 校验；Octop 侧契约测试进 CI。
- DB：SQLite/PG 双跑；迁移矩阵；备份/恢复；全局恢复禁用写服务。
- 前端：工作台列表/详情/新建/状态轮询；Windows CI 兼容。

### 12.3 发布门槛

- `make all`；
- 前端 `npx tsc -b`；
- 扩展服务契约测试 + smoke；
- 手动安装演练一次。

---

## 13. 安全与合规

- 写服务：Owner 开启、白名单、service token、幂等、限流、审计、公开禁写；转发时带 actor/app/tenant 头，扩展服务做 run owner 二次校验。
- 媒体：同源代理、Range、30–60 分钟 token、CSP 精确路径；`Cache-Control: private, no-store`；日志脱敏。
- SSRF：复用 `infra/utils/ssrf_guard.py` 的 `validate_https_url(_resolved)`；自托管需要 loopback/LAN 时，用显式的 per-install 管理员开关，默认仍拒绝私网。
- 凭证：service token/Provider Key 加密；不下发页面；扩展→Octop 回调使用独立的 HMAC secret，不复用 Octop→扩展的 service token。
- 数据：自托管优先；DPA/保留期/删除；对象存储加密。
- 审计：写调用、确认、下载、管理员覆盖；Octop `audit_log` 与扩展 `extension_audit` 用 op_id 关联。
- 合规：内容安全记录、AIGC 标识、广告法、肖像/素材授权。
- 备份：Extension Bundle/扩展服务侧只存声明、不存 secret；Octop 系统级备份是整库快照，包含 `secrets` 表，需在文档中如实说明；恢复后写服务 disabled。
- 已知限制：`audit_log` 可清理；需要不可篡改审计时单独设计。

---

## 14. 风险与对策

| 风险 | 对策 |
|---|---|
| 两套视频架构并存 | 文档声明本方案取代旧文 in-Octop 落点；V1/V2 分期；不并行实现 |
| 投影被页面篡改 | 列级 `write_policy` 或状态不投影；扩展服务权威；CAS/`status_seq` |
| 053 schema 债 / 迁移撞号 | MVP 不建市场表；实现时与预算迁移统一编号；不写死 052/053 |
| 写服务安全 | 能力位默认关、Owner 开启、白名单、幂等、审计、限流 |
| 视频 CSP | 只放行精确 media 路径 + 30–60 分钟 token |
| 512KB HTML | 单页 + 抽屉；原生 JS/Preact；超限再迁 Extension Workbench |
| 20k/5MB | 一行一 run；详情/事件/用量在扩展服务 |
| Provider 配额 | 队列 + 并发闸门 + 预算 |
| 手动安装复杂 | Docker Compose + healthcheck + smoke + 文档 |
| 数据合规 | 自托管优先；DPA/保留期/删除；Provider Key 客户自带 |
| 市场过早 | MVP 不做市场，只保留 Provider 接口和 Bundle 格式 |
| 多 run 冲突 | run owner + 状态机 + 幂等 + 服务端校验 |

---

## 15. 待决策

以下为 2026-11-23 评审后仍需业务方拍板的事项；其余已在本稿 §0.1 固定（单实例单企业、SQLite/PG 双后端、离线 License + BYO Provider Key、不做项目级 RBAC、MCP 后置）。实现前的阻断项与优先级见 §16。

1. **分期**：接受 V1（流水线验证）→ V2（团队工作台），还是因客户合同直接做 V2？
2. **状态投影**：列级 `write_policy`（推荐，保留本地列表/数据 Tab）还是状态不投影、列表读服务（MVP 更快）？
3. **上传**：V1-B 已按 50MB 落地；200MB/500MB 何时做分片续传？
4. **License 范围**：首个客户是否要求席位/离线到期管理？若否，V2 的 License 可再简化。
5. **V1 界面**：只做 MiniApp 审片台，还是同时做 React 审片台？
6. **工期/团队**：V1/V2 的目标日期与人员；Provider 联调窗口。

---

## 16. 评审结论与优先级（2026-11-23）

> 本节是评审建议，不替代 §15 待决策。P0 未拍板前，不建议按 V1 的 6–8 周排期直接开工。

### 16.1 总体判断

方向合理：外部扩展服务、扩展 DB 权威、复用 ingress/`usage_log`/`notifications`、能力位默认关闭，这些原则都应保留。

但当前稿还不能直接进入实现验收，主要卡在四个地方：

1. V1 没有可用的“创建 run / 回传审片决定 / 上传参考视频”入口；
2. 投影回写方案与现有 `app.import` 语义不兼容；
3. 同一行存在两个投影写入者，缺少 `status_seq`、单写者和对账；
4. 工作区代码已提前出现部分 V2 实现（`SERVICE_PROVIDERS` 已含 `extension`、`external.write`、052 迁移、invoke API），但 extension 读接口、上传和媒体代理还没完成，文档与代码基线不一致。

**结论：先冻结范围和基线，再补 V1 闭环；不要先做 `write_policy`、完整工作台或市场。**

### 16.2 P0：先做决策（不写业务代码）

| 顺序 | 决策 | 原因 |
|---|---|---|
| 1 | **冻结 052 迁移** | 工作区已有未提交的 `052_light_app_external_write`，与计划“不要写死 052/053”冲突；同时 S1 预算治理也计划使用 052+。先决定折叠进当前未发布迁移，还是让预算另起编号 |
| 2 | **V1 不投影系统列** | 推荐 V1 的 `runs` 只读服务列表/详情，不把 `status`/`asset`/`error` 放进 MiniApp 表。可一次性消除整行覆盖、`status_seq`、双写者、rules 拦截、5MB 限额和列级 `write_policy` 六个问题 |
| 3 | **重新定义 V1 闭环** | 当前 V1 说不做 `external.write`，但又要页面提交/审片回传/参考视频上传。二选一：<br>• V1 只做“管理员预置 run + 只读审片”<br>• 或 V1 开放窄写工具 `runs.create` / `review.decide` |
| 4 | **确定上传路径** | 200MB 文件不能走 iframe `postMessage`。二选一：宿主文件选择器，或 Octop 直传 + 短时 upload token；MVP 也可先限制 50MB 或只支持预置素材 |

### 16.3 P1：V1 必须先补的实现

按依赖顺序：

1. **Extension 只读 `call()`**
   - 当前 `call_service` 只走 `_external_tool_runner`，extension 的 `connector_instance_id` 为空，`runs.list` / `runs.get` 跑不通。
   - 需要增加 `provider=extension` 的读分支、工具 schema 校验、TTL 缓存和测试。

2. **最小写入口**
   - 如果 V1 需要页面提交，不要等完整 V2：只允许 `runs.create`、`review.decide` 两个窄写工具；`external.write` 仍默认关闭、Owner 开启。
   - 如果坚持 V1 不写，必须提供管理员 CLI/种子数据，并在验收里明确“页面不能创建 run”。

3. **审片结果回传**
   - 不能依赖扩展服务“读取 MiniApp 行数据”，当前没有这条路径。
   - 用 `review.decide` 写工具，或定义 Octop → 扩展的 outbound 回调；需要 HMAC、幂等和重试。

4. **上传与媒体代理**
   - 补 `/services/{name}/upload` 和 `/media/{asset}`。
   - 流式转发、Range/206/HEAD、50MB 限额、临时文件清理、磁盘配额、反向代理超时。
   - 不要先做“页面把大文件全量传给宿主”的假流式方案。

5. **确认机制**
   - 代码目前只判断是否存在 `confirmation_role`，没有校验调用者是否为 `app_owner` / `run_owner`。
   - 如果确认是安全审批，必须放到 Dashboard/HITL；如果只是防误触，页面令牌可以保留，但文档要明确它不是安全边界。

6. **Operations 账本补 `unknown`**
   - 网络超时后扩展可能已执行，Octop 不能只标 `failed`。
   - 增加 `submission_unknown` / 对账 / 重试路径，避免重复提交或永久卡住。

7. **本地 Docker 的 HTTPS/SSRF**
   - 当前要求 HTTPS 且默认拒绝私网，但 Compose 通常是 `http://video:8000`。
   - 先定：显式私网白名单 + 自签 CA，或 TLS sidecar；否则安装第一步就失败。

8. **动态 CSP**
   - `APP_CSP` / `WRAPPER_CSP` 是静态常量，无法只对某个应用放行 media 路径。
   - 需要服务端按应用生成 CSP，Dashboard 同步使用，并测试无通配、无路径逃逸。

### 16.4 P2：保留投影 / V2 才做

- `runs` 表增加 `status_seq` / `synced_at`；
- 部分 upsert + CAS + 单一写者；
- `app.import` 不再整行覆盖；
- rules 对系统回写 bypass；
- 列级 `write_policy` 及其全路径 enforcement；
- 双写者、乱序、重复回调测试；
- 5MB 投影容量核算；
- 删除、备份、恢复的跨系统对账。

### 16.5 P3：再往后

- 完整工作台（新建向导、确认/选帧/取消/重试、通知）；
- License/席位/到期管理；
- 多 Provider 自动路由、fallback；
- 市场表、自动安装、MCP 门面；
- 管理页、Provider 配额、审计报表。

### 16.6 关键证据

| 问题 | 代码/文档证据 | 影响 |
|---|---|---|
| `app.import` 整行覆盖 | `src/octop/infra/apps/service.py:5165`、`:5394`；`validate_row` 会把未提供列补空 | 扩展只回写系统列会清空标题/创意/审片意见 |
| 投影缺 `status_seq` / `synced_at` | 本稿 §3.2 的 `runs` 表没有这两列；§3.5 又要求使用 | 无法实现乱序守卫，旧回调可回退状态 |
| 双写者 | 同步动作由 Octop 写投影，异步由 extension 经 ingress 写投影 | 初始行 source/source_key 不一致时会重复 append |
| extension 读路径缺失 | `src/octop/infra/apps/service.py:8125` 的 `call_service` 没有 extension 分支 | V1 审片台无法 `runs.list` / `runs.get` |
| 确认角色未校验 | `src/octop/infra/apps/service.py:8346` 只判断 role 是否存在 | 任何 `edit_data` 都可确认本应 `app_owner`/`run_owner` 的动作 |
| Operations 无 unknown | `052_light_app_external_write.sql` 的状态集合 | 网络超时后无法区分“未执行”和“已执行未返回” |
| 5MB 投影容量 | 20k 行 × 文本字段远超 5MB 均值 | `runs` 表可能在几千行时触顶 |
| 静态 CSP | `dashboard/src/components/MiniAppFrame/appCsp.ts:16`、`src/octop/infra/apps/sandbox.py:31` | 无法按应用注入 media origin/path |
| 本地 HTTPS/私网 | 当前 `validate_https_url_resolved` 默认拒绝私网 | Docker Compose 同机部署可能无法连接扩展服务 |
| 迁移撞号 | 未提交的 `052_light_app_external_write` 与 S1 预算迁移冲突 | 后续合并/上线容易出现版本断言和迁移顺序问题 |

### 16.7 建议的最小 V1 验收

在 P0 决策后，V1 至少应明确并验证以下之一：

- **V1-A 只读审片**：管理员预置 run；页面能读列表/详情、播放/下载媒体；审片决定通过 `review.decide` 或 outbound 回调回传；不承诺页面创建 run。
- **V1-B 窄写工作台**：只开放 `runs.create` / `review.decide`；页面可创建任务、上传参考视频、审片；其余写工具仍留到 V2。

无论选哪条，V1 验收都应覆盖：

- [ ] extension 读 `call()` 可用；
- [ ] 参考视频上传/播放/下载可用，Range/206/HEAD 正确；
- [ ] 审片决定能到达扩展服务，重复/乱序不产生副作用；
- [ ] 未授权 `external.write` 返回 403；
- [ ] 扩展停机/超时后操作可对账；
- [ ] 本地 Docker 安装可连通；
- [ ] 无系统列投影覆盖用户列。

### 16.8 复核更新（2026-11-23 实现后）

已处理：extension 只读 `call()`、确认角色（`app_owner`/`admin`）、operations `submission_unknown` + resolve 对账入口、动态 CSP、50MB 上传（4MB 分片会话）、媒体 Range、私网开关、前端 SDK、管理员配置 UI、`video-workbench` 系统模板。

仍待：`run_owner` 的 Octop 侧校验由扩展负责（文档已注明）、自动对账（当前是人工标记）、V2 投影/完整工作台/License。

---

## 附录 A：`runs` 状态机

```text
draft
  ├─ queued ──► analyzing ──► waiting_creative
  │                              ├─ confirm → generating_frames
  │                              └─ cancel  → cancelled
  ├─ generating_frames ──► waiting_frame
  │                              ├─ select  → generating_video
  │                              └─ cancel  → cancelled
  ├─ generating_video ──► done
  ├─ any non-terminal ──► cancelled
  ├─ any ──► failed / blocked / needs_review
  └─ failed / blocked ──► retry（同一 run 新 attempt，或复制新 run，二选一）
```

允许的编辑（只列用户动作；系统列由扩展服务回写）：

| 动作 | 允许状态 | 角色 | 结果 |
|---|---|---|---|
| 改标题/素材 | `draft` | run owner | 保持 `draft` |
| 提交 | `draft` | run owner / `edit_data` | `queued` |
| 改创意/文案 | `waiting_creative` | run owner / app owner/admin | 保持 `waiting_creative` |
| 确认创意 | `waiting_creative` | run owner / app owner/admin | `generating_frames` |
| 选首帧 | `waiting_frame` | run owner / app owner/admin | `generating_video` |
| 取消 | `queued`/`analyzing`/`generating_*`/`waiting_*` | run owner / app owner/admin | `cancelled` |
| 重试 | `failed`/`cancelled`/`blocked` | run owner / app owner/admin | 新 attempt 或新 run |
| 放行 Warning | `needs_review` | app owner/admin | 继续或 `blocked` |
| 复制新 run | `done`/`failed`/`cancelled` | `edit_data` | 新 run（`draft`） |

- 所有动作在扩展服务侧做 CAS（`expected_status` / `status_seq`）；Octop 只做前置校验。
- `rules_json` 只约束上表中的用户动作；扩展服务的系统回写不受页面 rules 限制，但必须走系统写入路径。

---

## 附录 B：服务声明示例

```json
{
  "name": "video",
  "provider": "extension",
  "mode": "write",
  "base_url": "https://video.example.com",
  "auth_mode": "service_token",
  "extension_id": "octop.video",
  "read_tools": [
    {"name": "runs.get", "method": "GET", "path": "/v1/runs/{run_id}", "input_schema": {"type": "object", "required": ["run_id"]}},
    {"name": "runs.list", "method": "GET", "path": "/v1/runs", "input_schema": {"type": "object"}},
    {"name": "jobs.get", "method": "GET", "path": "/v1/jobs/{job_id}", "input_schema": {"type": "object", "required": ["job_id"]}},
    {"name": "assets.get", "method": "GET", "path": "/v1/assets/{asset_id}", "input_schema": {"type": "object", "required": ["asset_id"]}},
    {"name": "assets.content", "method": "GET", "path": "/v1/assets/{asset_id}/content", "input_schema": {"type": "object", "required": ["asset_id"]}}
  ],
  "write_tools": [
    {"name": "runs.create", "method": "POST", "path": "/v1/runs", "input_schema": {"type": "object", "required": ["run_id", "title"]}},
    {"name": "review.decide", "method": "POST", "path": "/v1/runs/{run_id}/review", "input_schema": {"type": "object", "required": ["run_id", "decision"]}},
    {"name": "assets.upload", "method": "POST", "path": "/v1/assets/stream", "input_schema": {"type": "object"}},
    {"name": "frames.select", "method": "POST", "path": "/v1/runs/{run_id}/select-frame", "input_schema": {"type": "object", "required": ["run_id", "asset_id"]}},
    {"name": "video.submit", "method": "POST", "path": "/v1/runs/{run_id}/video", "input_schema": {"type": "object", "required": ["run_id"]}},
    {"name": "cancel", "method": "POST", "path": "/v1/runs/{run_id}/cancel", "input_schema": {"type": "object", "required": ["run_id"]}}
  ],
  "confirmation": {"video.submit": "run_owner", "cancel": "run_owner"},
  "timeout_seconds": 30,
  "max_request_bytes": 65536,
  "max_response_bytes": 5242880,
  "idempotency_required": true,
  "allow_private_network": false
}
```

- 调用方转发时附加 `X-Octop-Actor-Id` / `X-Octop-App-Id` / `X-Octop-Tenant` / `Idempotency-Key`；扩展服务用 actor 做 run owner 校验。
- V1-B 的 `video` 服务至少声明：读 `runs.list` / `runs.get` / `assets.content`，写 `runs.create` / `review.decide` / `assets.upload`；其余写工具（选帧、提交视频、取消）留到 V2。
- 保存声明时 API 用 `service_token`（write-only）传入 token，服务端加密存 `secrets` 并把 key 写入 `secret_ref`；读取声明只返回 `token_configured`，不返回 token 或 `secret_ref`。
- 同机 Docker Compose 且扩展服务走 `http://video:8000` 时，由管理员开启 `allow_private_network=true`；默认仍要求 HTTPS 且拒绝私网。
- `tenant_id` 在 MVP 只是安装标签，不是核心库的租户维度。

---

## 附录 C：API 示例

```http
POST /api/apps/app_123/services/video/invoke
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "tool": "runs.create",
  "args": {"run_id": "run_01H...", "title": "夏季新品"},
  "idempotency_key": "01J9Z5K8W3Q4M6N7P8R9S0T1V2"
}
```

```json
{"ok": true, "op_id": "op_...", "result": {"run_id": "run_01H..."}}
```

高风险动作（`video.submit`）第一次只返回确认挑战：

```json
{
  "confirmation_required": true,
  "op_id": "op_...",
  "confirmation_token": "cfm_...",
  "expires_at": 1732400000,
  "args_hash": "sha256:..."
}
```

页面确认后带令牌重发；`op_id` 用于轮询，读操作使用 GET：

```http
GET /api/apps/app_123/services/video/operations/op_...
```

```http
GET /api/apps/app_123/services/video/media/asset_abc?token=...
Range: bytes=0-1048575
```

---

## 附录 D：配置项

```ini
DATABASE_URL=postgresql://...
STORAGE_BACKEND=local
OCTOP_BASE_URL=https://octop.example.com
OCTOP_SERVICE_TOKEN=...          # Octop -> 扩展
OCTOP_CALLBACK_SECRET=...        # 扩展 -> Octop ingress HMAC，独立密钥
LICENSE_FILE=/opt/octop-video/license/license.json
PROVIDER_VOLCENGINE_API_KEY=...
PROVIDER_MINIMAX_API_KEY=...
MEDIA_PROVIDER=volcengine|minimax
VIDEO_MODEL=doubao-seedance-2-5-260628|MiniMax-H3|MiniMax-H3-Max
IMAGE_MODEL=doubao-seedream-5-0-260128|image-01|image-01-live
MAX_UPLOAD_MB=50
WORKER_CONCURRENCY=2
MAX_ACTIVE_RUNS_PER_USER=2
MAX_VIDEO_JOBS_GLOBAL=4
# Provider/model 支持 per-run 覆盖；env 只是默认值
```

---

## 附录 E：代码落点建议

| 关注点 | 位置 |
|---|---|
| 写服务 schema/逻辑 | `src/octop/infra/apps/` + `src/octop/infra/db/repos/`；新代码建议独立 `apps/services/` 子包，避免继续膨胀 `service.py` |
| 写服务 HTTP | `src/octop/api/routers/apps.py`（薄路由） |
| 能力位/桥 | `dashboard/src/components/MiniAppFrame/` + `infra/apps/capabilities.py`（新增 `external.write`，并加入前端 `METHOD_CAPABILITY`） |
| 列级写策略 | `infra/apps/models.py` 的 `Column` + `service.py` 的 insert/update/import 路径 + Agent 工具 |
| 状态回写 | `infra/apps/ingress.py` + `infra/events/`（`light_app_ingress` / `app.import`） |
| 通知/用量 | `infra/apps/task_runner.py` 的通知模式；`usage_log`（051） |
| 工作台模板 | `src/octop/infra/apps/system_templates/video-workbench/`（`manifest.json` + `schema.json` + `app.html` + `preview.svg`） |
| 迁移 | 当前未发布迁移（实现时与 S1 预算迁移统一编号；**不要写死 052/053**） |
| 扩展服务 | 独立目录/仓库（FastAPI + Worker；MVP 可用 monorepo `services/video/`） |
| 文档 | `docs/video-workbench-implementation-plan.md` |

---

## 附录 F：参考

- `docs/miniapp-overview.md`：MiniApp 现状、限额、服务/事件/ingress。
- `docs/miniapp-product-plan.md`：定位、MCP Apps、媒体边界、阶段 1.5 的同源媒体设计。
- `docs/miniapp-ai-native-plan.md`：`light_app_tasks`、`usage_log` 归因、预算治理（S1）与市场后置。
- `docs/event-model.md`：app-owned ingress、`app.import` 幂等与顺序缺口。
- `docs/ai-video-commerce-system.md`：视频生产与合规背景；**其 §3.2/§3.3/§5 Sprint 2 的 in-Octop 落点已由本稿取代**。
- `docs/agent-os-roadmap.md`：单实例、非多租户定位。
- `src/octop/infra/apps/service.py`、`sources.py`、`ingress.py`、`webhooks.py`：现有外部服务/事件实现。
- `src/octop/infra/agents/settings/media_generation.py`：Provider 预设；MiniMax H3/H3-Max 已存在。
- `octop_harness/media/providers/minimax.py`：MiniMax 图片/H3 视频适配器与能力约束。
- `src/octop/infra/db/migrations/036_light_app_services.sql`：现有服务声明。
- H3-Image 不在本期范围；后续如接入，另立设计（官方文档：<https://platform.minimax.io/docs/guides/image-generation>）。
- `dashboard/src/components/MiniAppFrame/`：沙箱、桥、CSP、SDK。
