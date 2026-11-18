# Magic ERP/CRM 集成：技术原理与功能支持

> 日期：2026-11-17
> 调研基线：`dtyq/magic` master `0f1432c3`（2026-10-24 推送）+ 官方站点当前版本（magicrew.ai / letsmagic.cn）
> 范围：Magic 如何把 ERP / CRM / 企业内部系统接入 Agent；只分析连接、认证、配置、分发与治理，不评估商业版报价。
> 关系：补充 [Magic HTML 微应用与 MagicBase 对 MiniApp 的参考](./miniapp-magic-reference.md)、[Magic 商业版沙箱：官方宣传与开源实现的对照](./magic-sandbox-commercial-review.md)。
> 说明：本文只提炼设计，不复制 Magic 代码或文案。Magic 使用修改版 Apache 2.0（禁止未经书面许可的多租户 SaaS、要求保留品牌），与 Octop 的 MIT 不兼容。

---

## 0. 结论

**Magic 没有为 ERP/CRM 写专用连接器。** 它提供的是一套「**连接协议统一 + 凭据与配置分离 + 非 MCP 系统包装 + 数字员工分发**」的通用集成框架。

ERP/CRM 能不能接、怎么接，取决于对方暴露什么接口：

| 对方提供 | Magic 的接法 | 入口 |
|---|---|---|
| **MCP Server** | 注册外部 MCP Server（SSE / Streamable HTTP / Stdio） | MCP 连接器市场 |
| **OAuth2 REST API** | 注册 OAuth2 app，授权后用 `oauth2_request` 调 | Agent 工具 + 沙箱 OAuth2 注册表 |
| **HTTP + API Key** | 外部 MCP 静态 Header（`${ENV}`）或 Flow HTTP 节点 | MCP 配置 / Flow |
| **只有 HTTP，无 MCP** | Flow HTTP 节点 → FlowTool → Magic 托管 MCP | Flow + Magic 托管 MCP |
| **SOAP / 私有协议 / 数据库** | 自建 MCP Server，或写 Flow / Code Mode | 自建 |
| **只有 Excel 导出** | 数据分析 Agent 处理文件 | 非实时集成 |

成熟度判断：

| 能力 | 评分 | 判断 |
|---|---|---|
| 连接协议（MCP 三传输） | 8/10 | SSE / Streamable HTTP / Stdio 齐全；客户端处理了超时、重试、响应规范化 |
| 认证 | 8/10 | 无认证 / 静态 Header / OAuth2 / MCP Authorization 四种模式；MCP Authorization 做到了自动发现与动态客户端注册 |
| 每用户配置 | 8/10 | `require_fields` 支持按用户替换 URL/Header 占位符；OAuth2 按用户存 token |
| 非 MCP 系统包装 | 7/10 | Flow HTTP 节点 + FlowTool → Magic 托管 MCP，能把 REST 变成 Agent 工具 |
| 数字员工分发 | 7/10 | Agent 包 + 市场安装 + 项目级 MCP 绑定；但 **MCP 配置不随 Agent 包分发** |
| 写入治理 | 4/10 | 高风险确认靠 Agent 提示词和 `ask_user`，MCP 层没有强制审批 |
| 同步 / 字段映射 | 3/10 | 没有内置 ETL、字段映射、增量游标、CDC |
| 审计 | 4/10 | 有工具级 OTel 监控；没有看到 MCP 调用专用的业务审计表 |

**一句话**：连接层和认证层已经工程化；**同步、映射、写入审批、审计**这四层还停留在「Agent 自己完成」的阶段。

---

## 1. 官方口径与开源实际

### 1.1 官方宣传

`README_CN.md:52`、`:75`、`:132`：

> 跟 ERP、CRM 接不上 → 把内部系统封装成数字员工，全员可调用
>
> 将碎片化的内部系统（ERP/CRM/数据库）与业务经验，深度封装为**可全员调用的数字员工**。打破系统孤岛，让零散的接口调用沉淀为企业可规模化复用的核心数字资产。
>
> 凌晨想到一个问题：「华东区这个季度的毛利率还撑得住吗？」个人 AI 助理连接 ERP、财务系统、CRM，调用财务专家 Agent，30 秒后一张实时看板推到你面前。

中文企业版页面列出的「企业系统接入」包括：

- 钉钉：群聊、文档与待办一键接入
- 飞书：消息、云文档与日程直接可读
- 企业微信：会话、通讯录与日程会议打通
- 腾讯文档 / 金山文档：在线文档即上下文
- **ERP：订单、库存与采购数据接入**
- **CRM：客户与销售线索同步跟进**
- **业务数据库：销售与经营数据直接查询**
- 企业知识库、Teambition、企业邮箱、企查查

### 1.2 开源实际

在 `dtyq/magic` 开源仓库中：

- **没有** SAP、Salesforce、HubSpot、金蝶、用友、纷享销客等任何专用适配器；
- **没有** 官方 MCP Server 的种子数据；前端连接器市场读的是 `/api/v1/mcp/server/queries`，官方连接器应在部署侧或云数据库；
- 官方数字员工目录只有六个通用角色（`config/autoload/official_agents.php:9-149`）：`data_analysis`、`design`、`video-production`、`general`、`ppt`、`summary`，**没有 ERP/CRM 角色**。

所以，「ERP/CRM 集成」在开源版里是**能力框架**，不是**开箱即用的连接器**。

---

## 2. 总体架构

```
ERP / CRM / 数据库
   │
   ├─ ① 原生 MCP Server（SSE / Streamable HTTP / Stdio）
   │      └─ magic-service MCP Registry（组织级）
   │           ├─ 认证：None / 静态 Header / OAuth2 / MCP Authorization
   │           ├─ 每用户：require_fields + OAuth2 token / MCP credential
   │           └─ 项目绑定：ProjectMcpConfigService → mcpServers
   │
   ├─ ② OAuth2 REST API（Salesforce / HubSpot / 自建 CRM）
   │      └─ super-magic OAuth2 App Registry（沙箱内文件）
   │           ├─ 授权：PKCE + callback relay
   │           ├─ 凭证：refresh token 自动刷新
   │           └─ API 文档：OpenAPI 3.1 记录 → oauth2_request
   │
   ├─ ③ HTTP / API Key（金蝶、用友、内部 ERP）
   │      └─ Flow HTTP 节点 → FlowTool → Magic 托管 MCP
   │           └─ Agent 通过 MCP 调用，不需要知道 HTTP 细节
   │
   └─ ④ 任意 REST + 环境变量
          └─ env-manager + Code Mode（run_python_snippet / shell_exec）

Agent（super-magic）
   ├─ MCPServerManager：懒连接、工具发现、schema、调用、超时、重试
   ├─ using-mcp skill：模型按需加载，MCP 工具不直接挂到 tools 列表
   └─ 数字员工包：IDENTITY / AGENTS / SOUL / TOOLS / SKILLS / skills
```

核心分层：

1. **协议层**：MCP 作为统一工具协议，把不同系统的能力收敛成 tool。
2. **注册层**：Server 在组织级注册，项目按需绑定，用户按需配置。
3. **凭据层**：配置里只放引用，运行时再取真实 token。
4. **运行层**：懒连接 + Code Mode，避免工具 schema 污染主上下文。
5. **包装层**：Flow 把 REST 变成 FlowTool，再暴露成 MCP tool。
6. **产品层**：Agent 包只带角色与技能，连接与授权留在项目/用户层。

---

## 3. 核心技术原理

### 3.1 MCP 是统一的工具协议

MCP Server 支持四种类型（`backend/magic-service/app/Domain/MCP/Entity/ValueObject/ServiceType.php:17`）：

| 类型 | 说明 |
|---|---|
| `sse` | Magic 自己托管的 MCP Server，工具来自 Flow |
| `external_sse` | 外部 MCP Server，SSE 传输 |
| `external_http` | 外部 MCP Server，Streamable HTTP |
| `external_stdio` | 外部 MCP Server，stdio 子进程 |

Agent 侧客户端（`backend/super-magic/app/mcp/connection/client.py:4`）优先 Streamable HTTP，自动回退 SSE；支持 stdio；单次 `call_tool` 硬超时 300 秒；对 4xx/5xx 预读 body；把非标准 HTTP 响应规范化为 JSON-RPC error。

**关键设计：MCP 工具不挂到 LLM 的 tools 列表**，只通过 Code Mode 调用（`app/mcp/connection/server_manager.py:390`）。模型需要先加载 `using-mcp` skill，再写 `run_sdk_snippet` 调：

```
mcp_list_servers      → 列出当前 chat 的 MCP Server 与连接状态
mcp_connect_server    → 连接并返回真实工具列表
mcp_list_tools        → 列出已连接 Server 的工具
mcp_get_tool_schema   → 读取 JSON input schema
mcp_call_tool         → 实际调用
mcp_add_server        → 注册新 Server（stdio/http）
mcp_remove_server     → 断开并删除配置
```

这样做的好处是：

- 几十个 MCP 工具不会一次性塞满上下文；
- 工具 schema 按需读取；
- 多步调用可以在一个代码片段里完成，中间结果不回模型。

代价是模型必须走 skill 流程，不能直接「看到工具就调」。

### 3.2 三级配置与作用域

**Server 注册是组织级的**（`backend/magic-service/app/Domain/MCP/Entity/MCPServerEntity.php:18`）：

- `organization_code`、`code`、`name`、`description`、`icon`、`type`、`service_config`、`enabled`、`creator`；
- 官方组织的 Server 对所有组织可见（`MCPServerAppService.php:118` 的 `availableQueries`）；
- 非官方组织按 `ResourceType::MCPServer` 的操作权限过滤；
- 项目通过 user setting 绑定一组 Server（`ProjectMcpConfigService.php:57`）。

**用户配置是每用户的**（`MCPUserSettingEntity.php:15`）：

- `require_fields`：从 URL path/query 和 Header 值里提取 `${field}` 占位符，用户填自己的实例地址、API Key；
- `oauth2_auth_result`：旧 OAuth2 模式的 token；
- `additional_config`：扩展字段。

**会话配置是每 chat 的**（`backend/super-magic/app/mcp/store/chat_mcp_store.py:5`）：

- 路径 `chat_history_dir/mcp_servers.json`；
- 项目配置通过任务消息下发后 ingest 进来；
- 客户端也可以在消息里带 `mcp_config` 增量添加（`app/service/mcp_service.py:30`）；
- 配置变更通过 Horizon 通知模型「有新的 MCP Server，需要重新连接」。

这三层的好处是：**同一个 ERP MCP Server 可以被不同项目、不同用户复用，但每个用户用自己的凭据**。

### 3.3 凭据生命周期：配置里永远没有明文 token

这是 Magic 最值得借鉴的部分。

#### 路径 A：旧 OAuth2（`ServiceConfigAuthType::OAUTH2`）

- Server 配置里存 `oauth2_config`：`client_url`、`authorization_url`、`token_url`、`client_id`、`client_secret`、`scope`；
- 用户授权后，token 存 `MCPUserSetting.oauth2_auth_result`；
- 支持 refresh（`MCPOAuth2Client.php:119`）；
- 手动配置端点，不自动发现。

#### 路径 B：MCP Authorization（`ServiceConfigAuthType::MCP_AUTHORIZATION`）

这是代码里称为 “MCP 2026-07-28 Authorization” 的新模式，完整实现了 OAuth 2.1 的资源发现流程：

1. **发现**：`MCPAuthorizationDiscoveryClient.php:30` 按 RFC 9728 找 `/.well-known/oauth-protected-resource`，再按 RFC 8414 / OIDC 找 `/.well-known/oauth-authorization-server`；
2. **能力探测**：`MCPAuthorizationCapabilityService.php:41` 探测 Resource 的 401/403、`WWW-Authenticate`、授权端点、token 端点、注册端点、支持的 token 认证方式，返回 `ready / needs_configuration / unsupported / unavailable`；
3. **客户端注册**：`resolveClientRegistration`（`MCPAuthorizationAppService.php:447`）支持三种模式：
   - `pre_registered`：手工填 client_id/secret；
   - `dcr`：动态客户端注册（POST registration_endpoint）；
   - `cimd`：Client ID Metadata Document；
4. **授权**：`state` + PKCE S256 + `resource` 参数（RFC 8707），`state` 存 Redis，600 秒 TTL，一次性消费（`MCPAuthorizationStateStore.php:29`）；
5. **回调**：`MCPAuthorizationBindingService.php:53` 的公开回调不依赖 Magic 登录态，身份由一次性 state 恢复；
6. **存储**：access token / refresh token / client secret 用 AES-256-GCM 加密后存 `magic_mcp_authorization_credentials`（`MCPAuthorizationSecretCipher.php:20`）；
7. **运行时取用**：项目配置里只保留 `auth_type=2 + mcp_server_code` 的引用，`MCPAuthorizationReferenceBuilder` 会剥掉 `Authorization` 和 `token`（`MCPAuthorizationReferenceBuilder.php:26`）；super-magic 在建连前调用内部接口 `/mcp/authorization/credential/{code}` 取当前用户的最新 token（`credential_provider.py:17`），过期会自动 refresh。

#### 路径 C：静态 Header / API Key

- 外部 MCP 配置支持 `headers`、`token`、stdio `env`；
- 支持 `${VAR}` 占位符，运行时从 env-manager（个人/工作区加密 env 文件）解析（`env_resolver.py:27`）；
- 解析失败会报缺失变量名，不会把值写回配置；
- 错误文案会把 URL query、token、header 值脱敏（`redact_config_values`）。

#### 路径 D：OAuth2 App Registry（Agent 侧，非 MCP）

super-magic 自己还有一套 OAuth2 应用注册表，用于**任意 REST API**：

- `OAuth2AppDefinition`（`app/infrastructure/oauth2/app_definition.py:12`）：authorization_url、token_url、client_id/secret、scope、refresh_url、token 认证方式、字段名映射；
- `OAuth2AppRegistry`（`app_registry.py:13`）：按 app 存 `app.json`；
- `OAuth2CredentialStore`（`credential_store.py:83`）：按 app + subject hash 存 credential；
- `OAuth2ApiDocStore`（`api_doc_store.py:51`）：按 app 存 OpenAPI 3.1 JSON，支持 `oauth2_list_api_docs / get_api_doc / upsert_api_doc`；
- `oauth2_request`（`app/tools/oauth2/request.py:50`）：带 token 发 GET/POST/PUT/PATCH/DELETE，默认 `Authorization: Bearer`，可改成自定义 Header；禁止请求头里再带 Authorization；响应 256KB 上限、120 秒超时；
- 授权回调支持 `local` 和 `magic_service` 两种 relay（`callback_relay/factory.py:12`），沙箱里默认走 magic-service 中转。

**注意**：这是两套独立的 OAuth2 栈——MCP 的 OAuth2 在 magic-service，任意 REST 的 OAuth2 在 super-magic 沙箱文件里。两者不共享凭据库，这是 Magic 自身的一个架构重复点。

### 3.4 Flow 是「非 MCP 系统」的包装层

ERP/CRM 大多没有 MCP Server，Magic 用 Flow 补这一层。

**Flow HTTP 节点**（`HttpNodeRunner.php:32`）：

- 支持 GET/POST/PUT/DELETE；
- URL、Query、Path、Body、Header 都支持变量引用；
- 认证靠 Header 里写 `Authorization: Bearer ...` / `Basic ...` / API Key；
- 用 `ApiSend` 发请求，60 秒超时，SSRF 防御允许重定向；
- 没有 OAuth2 token 自动刷新，OAuth token 需要先用另一个 HTTP 节点获取。

**FlowTool → Magic 托管 MCP**（`MCPServerSSEAppService.php:26`）：

- 在 Magic 托管 MCP Server 里注册工具，`ToolSource::FlowTool`；
- 工具调用时执行指定的 Flow（`rel_code` / `rel_version_code`），把参数传入；
- 这样 Agent 看到的是一个标准 MCP tool，背后是 Flow 里的 HTTP 节点和业务逻辑；
- 这是「把内部系统封装成数字员工能力」的核心技术路径。

**限制**：Flow 里的 MCP Agent Plugin（`MCPAgentPlugin.php:23`）会跳过需要 OAuth2、MCP Authorization、`require_fields` 的 Server，也跳过 stdio（`:78-86`）。所以**Flow 内嵌 LLM 只能调用无认证或静态 Header 的 MCP Server**；需要按用户 OAuth 的 ERP/CRM，必须走 Agent 运行时，不能走 Flow 的 MCP 插件。

### 3.5 数字员工包与连接解耦

Agent ZIP 的结构（`AgentZipParser.php:20`）：

```
{agent_dir}/
├── IDENTITY.md   (name/role/description/llm frontmatter)
├── AGENTS.md     (system prompt)
├── SOUL.md       (personality)
├── TOOLS.md      (tools list)
├── SKILLS.md     (skills list + market_skills)
└── skills/       (skill sub-packages)
```

**包里没有 MCP Server 配置**。Agent 市场安装只复制角色、提示词、技能；连接由项目级 MCP 绑定和用户授权决定。这样设计的好处是不会把 ERP 凭据打进 Agent 包，坏处是「装了数字员工就能用 ERP」并不成立——还需要项目绑定 MCP + 用户授权。

Agent 版本实体有 `prompt` 和 `tools`（`AgentVersionEntity.php:22`），技能通过 `AgentSkillEntity` 关联；市场有 `AgentMarketEntity`（安装数、发布者类型、分类、发布状态）。官方数字员工只有六个通用角色，没有 ERP/CRM 专用角色。

### 3.6 治理：有作用域和审核，但缺 MCP 级审批/审计

已有：

- 组织级 Server 注册 + 官方组织可见；
- 按 `ResourceType::MCPServer` 的操作权限（读/写/删）；
- 项目级绑定；
- 每用户 `require_fields` / OAuth2 / MCP credential；
- 数字员工发布有审核状态（`ReviewStatus`）与市场发布目标；
- 工具级 OTel 监控（tool_monitoring：成功/失败/耗时/错误堆栈）。

缺的：

- MCP 工具调用没有独立业务审计表（谁在何时调了哪个 ERP 接口、写了什么）；
- 写入审批不是强制门禁，靠 Agent 提示词里的 `ask_user` 高危规则；
- 没有幂等键、去重、重试策略（连接重试 1 次，工具调用不重试）；
- 没有字段映射、增量同步、CDC、失败补偿；
- 没有 MCP 工具 schema 漂移检测；OpenAPI 文档靠手动 `oauth2_upsert_api_doc` 记录。

---

## 4. 功能支持矩阵

| 能力 | 支持情况 | 细节 |
|---|---|---|
| 传输协议 | ✅ | 外部 SSE、Streamable HTTP、Stdio；Magic 托管 SSE |
| 认证：无 | ✅ | `auth_type=0` |
| 认证：静态 Header/API Key | ✅ | `headers` + `${ENV}` 占位符；stdio `env` |
| 认证：OAuth2（MCP） | ✅ | 手动配置端点，token 存 user setting，支持 refresh |
| 认证：MCP Authorization | ✅ | Discovery + DCR/CIMD/pre-registered + PKCE + resource + refresh |
| 认证：OAuth2（任意 REST） | ✅ | Agent 侧 App Registry + `oauth2_request` + OpenAPI 文档 |
| 每用户实例地址/密钥 | ✅ | `require_fields` 替换 URL/Header 占位符 |
| 工具发现 | ✅ | `mcp_list_servers` → `connect` → `list_tools` → `get_tool_schema` |
| 工具调用 | ✅ | `mcp_call_tool`，JSON Schema 参数 |
| 大结果处理 | ✅ | result_saver 落文件，避免全量进上下文 |
| 超时 | ✅ | 连接 30s+；调用 300s；OAuth2 REST 120s；Flow HTTP 60s |
| 重试 | ⚠️ | 仅连接重试 1 次；工具调用不重试 |
| 错误规范化 | ✅ | 非标准 HTTP 响应转 JSON-RPC error；错误 body 预读 |
| 敏感值脱敏 | ✅ | URL query / token / header 在错误文案中打码 |
| SSRF | ⚠️ | 默认黑名单只封云元数据 IP；允许私网 ERP 地址；模板变量跳过校验 |
| 组织/项目作用域 | ✅ | 组织注册、项目绑定、官方组织可见 |
| 发布审核 | ✅ | 数字员工/Skill 有 review status |
| 写入审批 | ⚠️ | 靠 Agent 提示词 + `ask_user`，非强制 |
| 业务审计 | ⚠️ | 工具级监控有；MCP 调用业务审计无 |
| 字段映射/ETL | ❌ | 无内置；靠 Agent 或 Flow 表达式 |
| 增量同步/游标 | ❌ | 无内置；Flow Routine 可定时，但无游标/幂等 |
| Schema 漂移检测 | ❌ | 无；API 文档手动维护 |
| 连接器市场 | ✅ | 前端 ConnectorMarket 读 MCP Server 列表；官方连接器在部署侧/云 |
| 数字员工分发 | ✅ | Agent ZIP + 市场安装；**不含 MCP 配置** |

---

## 5. 典型 ERP/CRM 接入方案

### 5.1 SaaS CRM（Salesforce / HubSpot / Zoho）

两条路：

- **有官方 MCP Server**：注册外部 MCP，选 OAuth2 或 MCP Authorization，用户在设置页授权；
- **没有 MCP**：Agent 侧注册 OAuth2 app → 用户授权 → 记录 OpenAPI 文档 → `oauth2_request` 调用。适合销售线索查询、客户同步、活动写入。

### 5.2 金蝶 / 用友 / 内部 ERP（REST + API Key）

- 注册外部 MCP Server（Streamable HTTP/SSE），Header 用 `${ERP_API_KEY}`，URL 用 `${ERP_BASE_URL}`；
- 用 `require_fields` 让每个用户填自己的实例地址和密钥；
- 或者用 Flow HTTP 节点包装成 FlowTool，再挂到 Magic 托管 MCP；
- 适合订单查询、库存查询、采购单创建。

### 5.3 SAP / 老 ERP（SOAP / 私有协议）

开源版没有原生适配：

- 自建 MCP Server（用自己熟悉的语言写一层 MCP 封装），Magic 作为外部 MCP 接入；
- 或写 Flow HTTP 节点处理 SOAP XML；
- 需要自己做 WSDL 解析、字段映射、错误码转换。

### 5.4 数据库（MySQL / PostgreSQL）

- 英文官网列出的 MySQL/PostgreSQL 连接器属于官方 MCP Server（部署侧数据），开源仓库没有种子；
- OSS 可自建 MCP Server，或让 Agent 用 Code Mode 连数据库（不推荐直接给 Agent DB 凭据）；
- 更适合把 DB 查询封装成 FlowTool/MCP tool，做只读视图。

### 5.5 只有 Excel 导出

不是实时集成。用 data_analysis / general Agent 处理上传的 Excel/CSV，或用 MagicBase 做台账；这属于「文件集成」，不是 ERP/CRM 连接。

---

## 6. 主要局限与坑

1. **没有开箱即用的 ERP/CRM 连接器**：开源仓库没有专用适配器，官方连接器在云/部署侧。
2. **数字员工不携带连接配置**：安装 Agent 不会自动绑定 MCP；需要项目级配置 + 用户授权。
3. **两套 OAuth2 栈**：MCP OAuth2（magic-service）与任意 REST OAuth2（super-magic 沙箱文件）不共享凭据。
4. **Flow 的 MCP 插件不支持授权型 Server**：需要 OAuth2/MCP Auth/require_fields 的 ERP/CRM 不能走 Flow 内嵌 LLM。
5. **写入没有硬门禁**：MCP 调用不会强制 `ask_user`；是否确认取决于 Agent 的提示词和工具策略。
6. **无幂等/去重**：工具调用失败不会自动重试，重试可能重复下单/重复写 CRM。
7. **无字段映射/同步引擎**：复杂 ERP 字段映射、增量同步要自己写 Flow/Skill/代码。
8. **SSRF 不是网络边界**：默认只封云元数据 IP，私网 ERP 地址可访问；模板变量还会跳过校验。
9. **审计不足**：工具级 OTel 有，但「谁调了哪个 ERP 接口、改了什么」没有业务审计表。
10. **MCP Authorization 依赖对方支持**：很多 ERP/CRM 没有 RFC 9728 元数据，只能退回静态 Header/OAuth2 手动配置。

---

## 7. 对 Octop 的借鉴建议

Octop 已有 `infra/connectors`（catalog、OAuth、MCP gateway、credential crypto），比 Magic 的起点更集中。建议：

1. **统一连接抽象**：定义一个 `Connector` / `McpServer` 注册模型，字段包含 `transport`（SSE/HTTP/stdio）、`auth`（none/header/oauth2/mcp-authorization）、`scope`（org/project/user）、`required_fields`。
2. **配置只放引用，运行时取 token**：不要把 access token 写进项目配置；用 `credential_ref` + 内部接口取用，AES-GCM 加密，state 一次性消费。
3. **每用户 `require_fields`**：ERP 的实例地址、API Key 按用户填，URL/Header 用 `${field}` 占位符。
4. **懒连接 + 按需工具发现**：不要把 MCP 工具全量挂到 LLM tools；走 skill/Code Mode，读 schema 再调用。
5. **包装层**：Octop 的 `light_app_services` 或工具集可以增加「service → MCP tool」适配器，把内部 REST 暴露成标准 tool；对应 Magic 的 FlowTool。
6. **连接与专家包解耦**：专家/团队包只带角色、提示词、技能；连接绑定在 agent/project 运行时，授权按用户。
7. **补 Magic 缺的四件事**：MCP 调用审计、写入审批硬门禁、幂等键、出口白名单。
8. **统一 OAuth2 栈**：Octop 不要走 Magic 的「两套 OAuth2」老路，MCP 与 REST 共用一套 credential/consent store。

目标架构草图：

```
Connector Registry（统一）
  ├─ transport: mcp_sse | mcp_http | mcp_stdio | rest | service
  ├─ auth: none | header | oauth2 | mcp_authorization
  ├─ scope: org | project | user
  ├─ required_fields: instance_url / api_key / tenant
  └─ credential_ref（不含明文）

Credential / Consent Store（统一）
  ├─ OAuth2 / MCP Authorization / API Key
  ├─ AES-GCM 加密 + 一次性 state + refresh
  └─ 按 user + connector 维度

Runtime Adapter
  ├─ MCP client（懒连接、schema、调用）
  ├─ REST client（oauth2_request / service）
  └─ Tool wrapper（把 connector 暴露成 agent tool / MCP tool）

Governance
  ├─ 工具调用审计（actor / connector / action / payload hash / result / cost）
  ├─ 写入审批（prepared operation / ask_user）
  ├─ 幂等键与重试策略
  └─ 出口白名单与网络策略
```

---

## 8. 证据索引

| 主题 | 路径 |
|---|---|
| 官方 ERP/CRM 口径 | `README_CN.md:52`、`:75`、`:132`；letsmagic.cn/enterprise「企业系统接入」 |
| MCP Server 类型 | `backend/magic-service/app/Domain/MCP/Entity/ValueObject/ServiceType.php:17` |
| MCP 认证类型 | `backend/magic-service/app/Domain/MCP/Constant/ServiceConfigAuthType.php:10` |
| MCP Server 实体 | `backend/magic-service/app/Domain/MCP/Entity/MCPServerEntity.php:18` |
| MCP 配置构建 | `backend/magic-service/app/Application/MCP/Utils/MCPServerConfigUtil.php:30` |
| 外部 MCP 配置校验/SSRF | `.../ServiceConfig/ExternalSSEServiceConfig.php:117` |
| 用户设置 | `backend/magic-service/app/Domain/MCP/Entity/MCPUserSettingEntity.php:15` |
| MCP Authorization 授权 | `.../MCPAuthorizationAppService.php:68` |
| Discovery（RFC 9728/8414） | `.../Discovery/MCPAuthorizationDiscoveryClient.php:30` |
| 能力探测 | `.../MCPAuthorizationCapabilityService.php:41` |
| 动态客户端注册 | `.../MCPAuthorizationAppService.php:447` |
| 回调绑定 | `.../MCPAuthorizationBindingService.php:53` |
| 凭据加密 | `.../MCPAuthorizationSecretCipher.php:20` |
| state 一次性存储 | `.../State/MCPAuthorizationStateStore.php:29` |
| 凭据引用构建 | `.../Utils/MCPAuthorizationReferenceBuilder.php:26` |
| 运行时凭据接口 | `backend/magic-service/config/routes-v1/super-magic-open-api.php:96` |
| 项目级 MCP 配置 | `.../SupperMagicMCP/ProjectMcpConfigService.php:57` |
| Magic 托管 MCP 工具（FlowTool） | `.../Service/MCPServerSSEAppService.php:26` |
| Flow HTTP 节点 | `.../NodeRunner/Http/V1/HttpNodeRunner.php:32` |
| Flow MCP 插件限制 | `.../BuiltIn/AgentPlugin/MCP/MCPAgentPlugin.php:78-86` |
| Agent 包结构 | `.../Parser/AgentZipParser.php:20` |
| Agent 市场实体 | `.../Entity/AgentMarketEntity.php:21` |
| 官方数字员工目录 | `backend/magic-service/config/autoload/official_agents.php:9-149` |
| Agent 侧 MCP 管理器 | `backend/super-magic/app/mcp/connection/server_manager.py:24` |
| MCP 客户端传输 | `backend/super-magic/app/mcp/connection/client.py:4` |
| 懒连接与凭据解析 | `.../server_manager.py:141`、`:342` |
| env 占位符解析 | `backend/super-magic/app/mcp/config/env_resolver.py:27` |
| chat 级 MCP 配置 | `backend/super-magic/app/mcp/store/chat_mcp_store.py:5` |
| MCP 消息摄取 | `backend/super-magic/app/service/mcp_service.py:30` |
| OAuth2 App 注册表 | `backend/super-magic/app/infrastructure/oauth2/app_registry.py:13` |
| OAuth2 API 文档 | `.../api_doc_store.py:51` |
| OAuth2 请求工具 | `backend/super-magic/app/tools/oauth2/request.py:50` |
| OAuth2 回调 relay | `.../callback_relay/factory.py:12` |
| MCP 工具仅 Code Mode | `backend/super-magic/app/mcp/connection/server_manager.py:390` |

---

## 9. 待定问题

1. **连接器抽象**：Octop 是把 MCP 当作唯一 connector 协议，还是保留「MCP + REST + service」三种并存？
2. **凭据归属**：ERP/CRM 用「每用户 OAuth2」还是「服务账号 + 行级权限」？前者审计清晰但授权成本高，后者配置简单但权限边界弱。
3. **写入门禁**：MCP 工具调用是否要像 MiniApp 一样强制 prepared operation + `ask_user`？还是按工具风险等级分三态？
4. **幂等策略**：ERP 下单/CRM 写入的幂等键由谁生成——连接器、Agent 还是业务表？
5. **网络边界**：私网 ERP 地址是否要走统一的出口代理/白名单，而不是依赖 SSRF 黑名单？
