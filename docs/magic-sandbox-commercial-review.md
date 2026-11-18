# Magic 商业版沙箱：官方宣传与开源实现的对照

> 日期：2026-11-17
> 调研基线：`dtyq/magic` master `0f1432c3`（2026-10-24 推送）+ 官方站点当前版本（magicrew.ai / letsmagic.cn）
> 范围：只调研「沙箱 / 隔离 / 出口 / 治理」这一层的**官方宣传口径**与**开源仓库里能验证的实现**。
> 关系：补充 [Magic HTML 微应用与 MagicBase 对 MiniApp 的参考](./miniapp-magic-reference.md)；不替代 [业务 AgentOS 实施路线](./agent-os-roadmap.md) 的 M2.1「执行内核」。

---

## 0. 结论

**Magic 的沙箱宣传由三层文案构成**：README 的七大能力（`README_CN.md:90`）、中文官网企业版「安全与治理」（letsmagic.cn/enterprise）、英文站「隐私与安全」（magicrew.ai）。

**逐条核对后的实际状态**：

| 类别 | 宣传点 | 状态 |
|---|---|---|
| ✅ 真实落地 | 独立沙箱容器 / 每任务独立 Pod | `agent_builder.go:81` `BuildSandboxPod` |
| ✅ 真实落地 | 资源与数据隔离（配额 + 多租户字段） | `config.go:103-118`、`MagicBaseRowEntity` 双 organization 字段 |
| ✅ 真实落地 | 凭据不落沙箱 + 出口白名单 | `backend/magic-gateway/`（本次最有价值的发现） |
| ✅ 真实落地 | 全链路审计留痕 | `app/infrastructure/observability/` + `AuditLogEntity` |
| ⚠️ 部分落地 | Sidecar 网络代理按用户分流流量 | 身份隔离有，网络代理 sidecar 形态无 |
| ⚠️ 部分落地 | 插件安全审查机制 | 有 `skill-vetter` 协议，但是提示词级、非强制门禁 |
| ⚠️ 部分落地 | 发布审核 | 有绑定式人工确认，审核主体是发布者本人 |
| ❌ 仅宣传 | 与主系统不同 VPC、私网终端节点 | 全仓无 VPC / NetworkPolicy 实现 |
| ❌ 仅宣传 | 用量上限按组织/部门/成员设定、超支可见 | `OrganizationConfigEntity::checkAmount()` 方法体被注释 |

**给 Octop 的结论**：可以直接借鉴的是**凭据代理 + 出口白名单 + 资源配额默认值 + 沙箱生命周期 GC + 输入路径校验**这五件；「VPC / Sidecar / 组织架构同步 / 模型管控 / 组织级发布审核」属于 Octop 明确排除的多租户 SaaS 方向，**不建议追**。

---

## 1. 宣传口径原文

### 1.1 README（中英双语同源）

中文（`README_CN.md:90`）：

> 每个 Agent 运行在**自研沙盒容器**中，与主系统处于不同 VPC，通过私网终端节点连接，天然隔绝越权访问。Sidecar 网络代理为每位用户独立管控流量，多租户之间**资源与数据完全隔离**。严格的**插件安全审查机制**在上架前拦截恶意代码，让企业数据始终在可信边界内运转。

英文（`README.md:90`）措辞略有不同：

> Every Agent runs inside a **proprietary sandbox container**, isolated from the main system in a separate VPC and connected via private endpoints — zero risk of unauthorized access. A Sidecar network proxy manages traffic independently per user, with **complete resource and data isolation across tenants**. A strict **plugin security review** catches malicious code before publishing, keeping enterprise data within a trusted boundary at all times.

同一份 README 的「问题 → 方案」对照表（`README_CN.md:49-51`）：

> - API 费用说不清，月底超支 → 部门、用户、单次任务都能设预算上限
> - 员工私用第三方工具，核心数据存在泄露风险 → **自研沙盒隔离，数据不出可信边界**
> - AI 自动执行可能误删文件、发错邮件 → 高危动作必须人工审批，决策权在人

### 1.2 中文官网企业版「安全与治理」

页面：letsmagic.cn/enterprise。页首导语：「人人调用数字员工，经验沉淀为组织资产，**安全与成本全程可控**」；核心价值主张三句：「经验留得下，费用看得清，数据管得住」。

页首「企业管控台」列出的九大模块：企业管控台、模型管控、用量管控、功能权限、发布审核、组织与身份、审计与监控、**沙盒隔离**。

「安全与治理」三大分节原文：

| 分节 | 子项 | 原文 |
|---|---|---|
| **01 权限管控**（谁能用什么，由企业决定） | 组织与身份 | 同步组织架构，统一管理成员、部门和管理员 |
| | 功能权限 | 按部门、成员开放 AI 功能，按需收放 |
| | 模型管控 | 统一配置可用的大模型，指定谁能用哪一个 |
| **02 发布与运行**（上线有审核，运行有隔离） | 发布审核 | 数字员工、Skill 在内部发布前须经审核 |
| | **沙盒隔离** | **每个任务在独立沙盒中执行，彼此隔离，不触及主系统** |
| **03 审计与成本**（每一次调用都有据可查） | 审计与监控 | 大模型调用和后台操作全程留痕，随时追溯 |
| | 用量管控 | 按组织、部门、成员设定用量上限，超支提前可见 |

注意：官网企业版的措辞比 README **更克制也更接近工程实现**——「独立沙盒」「不触及主系统」「留痕」都是可验证的描述，没有提 VPC 与 Sidecar。

### 1.3 英文站「隐私与安全」

云服务口径三条：

> 01 **隔离执行**：每个任务都在独立沙箱中执行，绝不触碰不该存取的资料。
> 02 **加密储存**：你上传的文件和它产生的所有内容，都加密存放在你的私人空间。
> 03 **由你掌控**：任何重要操作——删除文件、寄送邮件——都要等你点头才会执行。

同页另有「90+ 官方连接器」清单（Notion、Linear、Figma、Stripe、Atlassian、MySQL、PostgreSQL、企查查等，分文档笔记/项目管理/设计/数据与开发/研究/金融/电商/日常八类），并说明「没有你要的？自建连接器」。

### 1.4 定价页的企业版（Self-Hosted）

`magicrew.ai/pricing` 页面内嵌数据（Team Plans 分区）：

| 字段 | 内容 |
|---|---|
| 名称 | **Self-Hosted** |
| 描述 | "Designed for Enterprises with High Data Security Requirements" |
| 亮点 | Data Sovereignty Control／Flexible Feature Customization |
| 权益 | Unlimited User Seats／Unlimited Feature Access／**Controllable User Concurrent Tasks**／Supports Custom Integration of Various AI Models／Scalable Cloud Storage／**Enterprise Data Sovereignty**／Dedicated Technical Consultant／**24/7 Support Consultation** |

定价页 FAQ 相关问答（原文）：

> **私有部署需要什么基础设施？**
> "Private Deployment supports major cloud platforms (AWS, Azure, GCP, Alibaba Cloud) or on-premises servers. We provide full deployment documentation, container images, and dedicated technical assistance."

> **数据安全如何保证？**
> "Enterprise Suite uses multi-tenant data isolation - each organization's data is stored completely separately. **Private Deployment keeps all data within your private environment; we have zero access.**"

档位结构：

| 档位 | 价格 | 并发任务 | 存储 |
|---|---|---|---|
| Free | 免费 | 1 | 1GB |
| Plus | $9.99/月 | 4 | 20GB |
| Pro | $24.99/月 | 6 | 40GB |
| Max | $49.99/月 | 10 | 160GB |
| Ultra | $99.99/月 | 20 | 320GB |
| **Team Plus** | **$49.99/席位/月**（年付原价 $600，标 83% Off） | 3/席位 | 1TB/席位 |
| **Self-Hosted** | Contact Us | 可控 | 可扩展 |

FAQ 还说明：Team Plan 与 Individual Plan 是**两套完全隔离的系统**，套餐、积分、数据存储都不共享、不可转移；Team 积分是共享池，管理员可为每个成员设月度上限。

### 1.5 三种部署形态（中文官网）

| 形态 | 官网措辞 |
|---|---|
| 云服务 | 注册即用／开箱即用／持续自动更新／适合快速试点 |
| **企业私有化部署**（标注"企业推荐"） | 私有化部署、专属模型接入、业务系统深度集成，满足大型企业的合规与安全要求／**部署在企业自有环境**／接入专属模型／对接 ERP、CRM 等业务系统／联系专属顾问 |
| 开源社区版 | 代码完整开源，采用 Apache 2.0 协议／数据完全自主／适合有技术团队的企业和开发者 |

---

## 2. 宣传与代码的逐条核对

| 宣传点 | 开源仓库实现 | 判定 |
|---|---|---|
| 独立沙箱容器 | `backend/sandbox-components/pkg/sandbox-gateway/template/agent_builder.go:81` `BuildSandboxPod` — 按 sandbox_id 建 Pod，容器为 fuse（可选）+ playwright + agent；`RestartPolicy: Never`；标签含 `sandbox-id` / `project-id` | ✅ 真实 |
| 每任务独立沙箱、不触及主系统 | `services/sandbox/sandbox_service.go:68` `createSandboxPod` + `:235` `newSandboxService`（每沙箱一个 Service）；Agent 通过 `http://localhost:8080` 访问同 Pod 内的 agfs sidecar | ✅ 真实 |
| 资源隔离 | `pkg/sandbox-gateway/config/config.go:103-118`：Agent 默认 `100m` CPU request / `2` limit、`300Mi` 内存 request / `2Gi` limit；fuse、qdrant 各有独立配额 | ✅ 真实 |
| 凭据不出沙箱 | `backend/magic-gateway/README.md`：容器拿不到环境变量原值，只能通过 API 代理间接使用；请求体里写 `env:VAR` / `${VAR}` / `OPENAI_*`，由网关替换后转发 | ✅ 真实（最有价值） |
| 出口可控 | `backend/magic-gateway/url_validator.go:37` — `MAGIC_GATEWAY_ALLOWED_TARGET_URLS` 白名单，支持 `exact` / `domain` / `prefix` / `regex` 四类规则 | ✅ 真实 |
| 身份隔离 | 网关注入 `magic-user-id` / `magic-organization-code`，签发 JWT（`main.go:53` 默认 30 天，`MAGIC_GATEWAY_TOKEN_EXPIRE_DAYS` 可配）；另有 `/api/ai-generated/sign` 用 Blake3 签名 | ✅ 真实 |
| Sidecar 网络代理按用户分流流量 | 网关有身份维度，但**按用户分流的代理 sidecar 部署形态在开源仓库不存在** | ⚠️ 部分 |
| 与主系统不同 VPC、私网终端节点 | 全仓 `grep VPC`：只命中 README 与阿里云 OSS 的 `x-oss-ac-vpc-id` 头；`grep NetworkPolicy` **零命中** | ❌ 仅宣传 |
| 多租户数据完全隔离 | `organization_code` + `data_organization_code` 双字段贯穿 `MagicBaseRowEntity`；`MagicBaseProjectStorageRouteEntity` 按 project 路由到独立 Mongo database/collection | ✅ 数据面真实 |
| 插件安全审查机制 | `backend/super-magic/agents/skills/skill-vetter/SKILL.md`（含 REJECT 清单、权限范围、LOW/MEDIUM/HIGH/EXTREME 分级、信任层级、报告模板） | ⚠️ 提示词级，非拦截器 |
| 发布审核 | `app/service/prepared_operation_confirmation.py` + `app/tools/super_magic/skills.py:212` `prepare_skill_publish` → 人工确认 → `publish_skill`，绑定快照 fingerprint、一次性消费、24h TTL | ⚠️ 有绑定式确认，但审核主体是发布者本人 |
| 高危动作必须人工审批 | `app/tools/ask_user.py:81`（含高危操作确认规则）、`app/tools/micro_app_plan.py:110`（计划审批）；写操作强制 `confirmed_by_user` | ✅ 真实 |
| 大模型调用与后台操作全程留痕 | `app/infrastructure/observability/`：OpenTelemetry span exporter、error tracking、websocket tracking、tool monitoring、LLM cost tracking；`AuditLogEntity` 带 TTFT / 总延迟 / provider / model 版本快照 / request_id / event_id | ✅ 真实且质量高 |
| 按组织、部门、成员设定用量上限、超支提前可见 | `app/Domain/ModelGateway/Entity/OrganizationConfigEntity.php`：字段 `totalAmount` / `useAmount` / `rpm` 存在，但 `checkAmount()`（:44）与 `checkRpm()`（:35）**整个方法体被注释** | ❌ 开源未生效 |
| 沙箱可运维（启停/升级/保活） | `app/tools/get_sandbox_info.py`、`restart_sandbox.py`；`SandboxKeepAliveEntity`（user/org/project/topic/sandbox/lastKeepaliveAt/failureCount）；`SandboxRestartPlanner` | ✅ 真实 |
| 沙箱 GC | `pkg/sandbox-gateway/gc/types.go:66` Agent 退出 10 分钟清理；四套 GC：Agent Pod / Fuse Pod / PVC / PV | ✅ 真实 |

---

## 3. 可借鉴的五个实现细节

### 3.1 凭据代理模式（最值得抄）

`backend/magic-gateway` 的设计：

1. 容器**永远拿不到** API key 原值，只有临时令牌；
2. 请求体里写 `env:OPENAI_API_KEY` / `${VAR}` / `OPENAI_*`，网关转发时替换为真值；
3. 目标 URL 必须命中白名单，scheme 逐跳校验防 SSRF（`url_validator.go` 的 `_ALLOWED_URL_SCHEMES`、最多 5 次重定向）；
4. 网关按 `magic-user-id` / `magic-organization-code` 下游做租户隔离，并签发 JWT。

**Octop 现状**：`infra/connectors/crypto.py` 有凭据加密；`infra/agents/workspace/execute_env.py` 把 `~/.octop/env` 与工作区 `.env` 合并后**直接注入执行环境**——凭据进了 Agent 进程。

**这就是 M2.1「执行隔离」里缺的那一半**：隔离只解决「Agent 崩了/跑飞了不影响别人」，凭据代理解决「Agent 即使被攻陷也拿不到你的密钥」。

### 3.2 资源配额默认值

| 容器 | CPU request | CPU limit | 内存 request | 内存 limit |
|---|---|---|---|---|
| Agent | 100m | 2 | 300Mi | 2Gi |
| fuse | 100m | 2 | 300Mi | 2Gi |
| qdrant | 可配 | 可配 | 可配 | 可配 |

这组数字可作为 Octop per-agent 执行信封的初始默认参考。**Octop 目前完全没有默认值**——`local_shell` 无并发上限、无内存上限、无墙钟上限。

### 3.3 沙箱生命周期与 GC

- `gc/types.go:66`：Agent Pod 退出后 10 分钟清理；
- 四套 GC 并行：Agent Pod / Fuse Pod / PVC / PV；
- `SandboxKeepAliveEntity` 记录 userId / organizationCode / projectId / topicId / sandboxId / lastKeepaliveAt / failureCount；
- `SandboxRestartPlanner` 负责镜像与沙箱重启规划。

**Octop 对应缺口**：Agent 是进程内对象，没有 pod、没有 keepalive、没有 GC 概念。这是「进程内 Agent → 隔离执行」的必然配套。

### 3.4 输入路径校验

`pkg/util/validator/path_validator.go`：

- 允许字符白名单 `^[a-zA-Z0-9_\-\.\/]+$`；
- 拒绝命令替换 `$(` / `${`；
- 拒绝路径穿越 `..`；
- 长度上限 1024；
- 检测 shell 元字符 `[;&|$`()\<>\\!"']`。

**「构造 Pod 之前先把所有外部输入校验一遍」这个实践**，应直接进 Octop 的 `infra/backend/` 与未来的 sandbox 相关代码。

### 3.5 白名单规则的可读格式

`MAGIC_GATEWAY_ALLOWED_TARGET_URLS` 用 `type:pattern@description|type:pattern@description` 表达（如 `domain:example.com@允许example.com|prefix:https://api.test.com/@允许test API`）。**带人类可读描述**，运维能看懂每条规则为什么存在——比纯正则列表好维护。Octop 做连接器白名单时值得照这个格式。

---

## 4. 需要注意的落差

**① 开源 ≠ 宣传。** Magic 的 README 描述的是**商业版/云端形态**，开源仓库是「可自托管的社区版」。做对标时不能把 README 当作 Magic 的 OSS 能力。

**② 「VPC 隔离」对 Octop 不适用。** Octop 的单节点自托管前提下没有 VPC 概念；Magic 宣传的这一点对应的 Octop 诉求是「进程内执行要隔离到子进程 / bwrap / Docker」。

**③ 「插件安全审查」是提示词不是门禁。** Magic 的 `skill-vetter` 由模型自觉执行；Octop 若要真门禁，得在安装路径上做静态检查 + 运行时沙箱，不能靠 skill 文案。

**④ 企业版三张王牌 Octop 无对位物**：组织架构同步（部门/成员）、模型管控（谁能用哪个模型）、组织级发布审核。这三条属于 Octop 文档里明确排除的「多租户 SaaS」方向，**不建议现在追**。

**⑤ Magic 的企业版部署门槛远高于 Octop。** K8s 集群 + MySQL + MongoDB + RabbitMQ + Redis + MinIO + 5 个服务（见 `cli/deployer/infra_registry.go` 的全套基建注册）。Octop 一条 `octop run` 就能起——这是 Octop 相对 Magic 最实在的优势，不要为了「对齐企业版」把它做重。

**⑥ 沙箱的隔离强度有天花板。** 开源实现里，Agent 容器**没有** SecurityContext（未设 `runAsNonRoot`、未设 `readOnlyRootFilesystem`、未设 `allowPrivilegeEscalation: false`、未 drop capabilities）；反而 fuse 容器是 `Privileged: true` + `SYS_ADMIN`（s3fs 需要）。也就是说：**隔离主要靠「一个任务一个 Pod + 网络命名空间」，而不是靠容器内权限收敛**。Octop 若参考，不应照抄这一层。

---

## 5. 给 Octop 的行动建议

| 优先级 | 动作 | 对应 Magic 实现 | 对应 Octop 缺口 |
|---|---|---|---|
| **P0** | 凭据代理 + 出口白名单 | `backend/magic-gateway/` | M2.1 执行内核：凭据现在直接进 Agent 进程 |
| **P0** | per-agent 执行信封默认值 | `config.go:103-118` | M2.1：并发/内存/墙钟/工具调用数全无上限 |
| **P1** | 沙箱生命周期与 GC | `gc/`、`SandboxKeepAliveEntity`、`SandboxRestartPlanner` | M2.2 监督与恢复 |
| **P1** | 外部输入路径校验规范 | `path_validator.go` | `infra/backend/` 各处缺统一校验 |
| **P1** | 出口白名单用「类型+描述」格式 | `url_validator.go` 规则格式 | 连接器/外部服务白名单尚无 |
| **P2** | Agent 容器权限收敛 | ——（Magic 未做，Octop 可做） | `runAsNonRoot` / `readOnlyRootFilesystem` / drop capabilities |
| **不做** | VPC / Sidecar 代理 / 组织架构同步 / 模型管控 / 组织级审核 | 仅宣传 | 属多租户 SaaS，Octop 明确排除 |

**一句话**：Magic 沙箱真正值得 Octop 学的是「**凭据与出口都不过 Agent 手**」和「**每个执行有硬配额和可回收生命周期**」；它宣传里最响的 VPC 与 Sidecar，在开源实现里并不存在，Octop 也不需要。

---

## 6. 证据索引

### 官方站点（2026-11-17 直接抓取）

| 来源 | 内容 |
|---|---|
| [magicrew.ai 中文产品页](https://www.magicrew.ai/zh-TW) | 「隐私与安全」三条；90+ 连接器清单 |
| [magicrew.ai 定价页](https://www.magicrew.ai/pricing) | Individual / Team Plus / Self-Hosted 权益；私有部署 FAQ；数据安全 FAQ |
| [letsmagic.cn 企业版](https://www.letsmagic.cn/enterprise) | 「安全与治理」三大分节；企业管控台九模块；三种部署形态 |
| [letsmagic.cn 首页](https://www.letsmagic.cn/) | 产品定位与 IM 接入说明 |

### 开源仓库（`dtyq/magic` master `0f1432c3`）

| 主题 | 路径 |
|---|---|
| 沙箱宣传原文 | `README_CN.md:90`、`README.md:90`、`README_CN.md:49-51` |
| 沙箱 Pod 构建 | `backend/sandbox-components/pkg/sandbox-gateway/template/agent_builder.go:81` |
| 沙箱生命周期 | `backend/sandbox-components/pkg/sandbox-gateway/services/sandbox/sandbox_service.go:68`、`:235` |
| 资源配额默认值 | `backend/sandbox-components/pkg/sandbox-gateway/config/config.go:103-118` |
| 沙箱 GC | `backend/sandbox-components/pkg/sandbox-gateway/gc/types.go:66` |
| 路径安全校验 | `backend/sandbox-components/pkg/util/validator/path_validator.go` |
| 凭据代理与出口白名单 | `backend/magic-gateway/README.md`、`url_validator.go:37`、`main.go:53` |
| 审计与可观测 | `backend/super-magic/app/infrastructure/observability/`、`backend/magic-service/app/Domain/Audit/ModelCall/Entity/AuditLogEntity.php:14` |
| 插件审查协议 | `backend/super-magic/agents/skills/skill-vetter/SKILL.md` |
| 审批与发布确认 | `backend/super-magic/app/service/prepared_operation_confirmation.py`、`app/tools/super_magic/skills.py:212`、`app/tools/ask_user.py:81`、`app/tools/micro_app_plan.py:110` |
| 用量校验（被注释） | `backend/magic-service/app/Domain/ModelGateway/Entity/OrganizationConfigEntity.php:35`、`:44` |
| 沙箱保活与重启 | `backend/magic-service/app/Domain/SuperMagic/Task/Entity/SandboxKeepAliveEntity.php:12`、`Service/SandboxRestartPlanner.php:35` |
| 部署基建清单 | `cli/deployer/infra_registry.go` |

### 第三方（仅作交叉参考，不作为事实依据）

- [Product Hunt 收录](https://www.productcool.com/product/magicrew)、[上线时间与创始人](https://vibecrowd.ai/repos/dtyq--magic)
- [Hysen Labs 评测](https://hysenlabs.com/projects/dtyq-magic) —— 指出「架构仅部分公开」「license 为 NOASSERTION」
- [2026 评测](https://swarm.beetlix.com/reviews/magicrew-review-2026) —— 指出「消耗率未公布，月度成本无法从文档推算」
- [MagiCrew 产品页](https://toolradar.com/tools/magicrew)、[与 Switch 对比](https://toolradar.com/compare/magicrew-vs-switch)
- [GitHub 仓库镜像页](https://awesome-repositories.com/r/dtyq-magic) —— 说明多数第三方站点在复述同一份 README

---

## 7. 待定问题

1. **凭据代理的落点**：Octop 是在 harness 侧做（进程内代理）还是在宿主侧做（HTTP 代理服务）？前者实现快但要求 Agent 走 HTTP；后者隔离更强但多一跳。
2. **执行信封默认值**：内存/CPU/墙钟的默认值按什么机型定？Magic 的 2Gi / 2 CPU 是 K8s 集群假设，单节点自托管需要重新标定。
3. **是否引入出口白名单**：Octop 的连接器已经能主动发外部请求（`external.fetch`、MCP），是否需要统一的出口管控层？
4. **沙箱形态**：Octop 走 Docker、bwrap 还是子进程？三者的隔离强度、可调试性与部署成本差别很大，需要在 M2.1 里拍板。
