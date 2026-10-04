# iTime AI 备用服务路由设计

版本：0.1；更新：2026-10-04。本文冻结 `REQ-AI-01/02` 中按能力配置备用服务、作业失败后有界
切换且不重置总截止时间的持久模型、管理 API、worker、成本额度和 Web 后台合同。它补充
[AI 作业与临时媒体设计](iTime-AI作业与临时媒体设计.md)、
[全局 AI 对话设计](iTime-全局AI对话设计.md)和
[AI 额度与成本账本设计](iTime-AI额度与成本账本设计.md)。

## 1. 目标与非目标

目标：

- 每个 AI 能力保留一个已发布主配置，并可按顺序配置最多两个备用配置。
- 新作业接单时冻结三次尝试实际使用的配置，后台之后修改路由不改变旧作业。
- 可重试网络、限流和上游故障进入下一条已冻结路由；总截止时间和最大尝试次数不重置。
- 每次 attempt、正文日志和 provider cost 可追溯实际配置版本；失败尝试成本仍由平台承担。
- 用户额度按路由中最坏价格/输出上限预留，成功后只按最终成功尝试的实际成本结算。
- 管理员可查看、调整备用顺序；只有同能力且最近测试成功、价格完整的版本可加入路由。
- 主配置发布/回滚和备用路由修改并发时有确定结果及审计记录。

非目标：

- 不跨能力降级，例如规划不能回退到普通对话配置。
- 不按单个用户、请求内容、地域或实时健康分流，不做权重、随机、熔断或流量比例。
- 不在本批新增供应商账号、地域元数据或生产出口探测；正式供应商和处理地区仍由部署阶段填写。
- 不对鉴权、端点策略、请求结构、空输出、非法工具结果等非重试错误自动切换。
- 不改变失败/取消不扣用户额度、失败尝试计入平台成本的既有规则。

## 2. 方案选择

评估三种方案：

1. **能力级路由配置 + 作业级不可变尝试快照（采用）**：后台保存当前备用顺序；接单时为每次
   attempt 写一条配置路由。具备数据库外键、成本归属和历史可复现性，代价是新增两张表及四类
   接单流程接入。
2. worker 每次失败后读取当前备用配置：实现较少，但后台改路由会改变在途作业，无法复现接单时
   条件，也可能在重试期间跨币种，不采用。
3. 在 `AIJob` 保存配置 ID JSON 数组：可冻结旧作业，但数据库无法约束配置存在、能力一致和顺序
   唯一，迁移及审计弱于关系表，不采用。

既有 `AIJob.config_version_id` 继续表示接单时主配置，兼容当前查询和历史数据；真正的每次调用
配置以新作业路由和 attempt 字段为准。

## 3. 能力级备用路由

`AIConfigCapability` 增加：

- `routing_version`：从 1 开始的乐观版本，主配置发布或备用顺序变化时递增。

新增 `AIConfigFallback`：

| 字段 | 语义 |
| --- | --- |
| `capability` | 与能力状态及配置版本一致 |
| `position` | 1 或 2，数值越小越先使用 |
| `config_version_id` | 备用的不可变 AI 配置版本 |
| `created_at / updated_at` | 路由审计时间 |

约束：

- `(capability, position)` 主键，`(capability, config_version_id)` 唯一；
- 配置 ID 与 capability 使用组合外键，删除或错误跨能力引用由数据库拒绝；
- 当前主配置不能同时出现在备用列表；
- 服务层只接受 `tested` 或 `retired`、最近测试成功、价格完整的配置；
- 当前额度策略存在时，主配置与全部备用必须使用相同币种。

备用身份不改变配置版本状态。处于备用路由中的 `tested/retired` 配置不可重新测试；管理员须创建
新版本测试后替换，避免正在被新作业引用的备用配置进入测试中间态。

发布或回滚某配置为主配置时：

1. 锁定能力状态和备用行；
2. 沿用既有测试、价格和单一 published 校验；
3. 若目标正在备用列表中，原子移除；
4. 切换主配置并递增 `routing_version`；
5. 不自动把旧主配置加入备用，避免隐藏改变故障路由。

## 4. 作业级不可变路由

新增 `AIJobConfigRoute`：

| 字段 | 语义 |
| --- | --- |
| `job_id / owner_id / capability` | 作业及账号、能力归属 |
| `attempt_number` | 1 至作业 `max_attempts` |
| `config_version_id` | 该次尝试必须使用的配置版本 |
| `created_at` | 接单时快照时间 |

组合外键同时约束 job owner、job capability 和 config capability。主键为
`(job_id, attempt_number)`。账号注销随作业级联清理。

当前 `max_attempts=3`，接单时按以下规则展开：

| 后台路由 | attempt 1 | attempt 2 | attempt 3 |
| --- | --- | --- | --- |
| 无备用 | 主 | 主 | 主 |
| 一个备用 | 主 | 备用 1 | 备用 1 |
| 两个备用 | 主 | 备用 1 | 备用 2 |

重复最后一个可用配置保留既有三次重试能力。未来若最大尝试数调整，仍按“超出路由长度后重复最后
一项”生成，不由 worker 临时猜测。

`AIJobAttempt` 和 `AIProviderCost` 均增加非空 `config_version_id`：

- claim 创建 attempt 时从 `AIJobConfigRoute` 同 attempt 行复制配置 ID；
- worker 的 claim 携带该 ID，四类 `load_*_work()` 只读取 claim 配置；
- cost 使用 attempt 配置的冻结价格，不再读取 `AIJob.config_version_id`；
- 管理成本列表按 provider cost 配置关联，显示实际模型和配置版本。

迁移为所有既有作业生成每次尝试均指向原 `AIJob.config_version_id` 的路由，并回填既有 attempt 和
cost，因此升级不改变历史结果。

## 5. 接单与额度

新增共享服务 `load_active_config_route(session, capability)`：

1. 以共享锁读取能力状态、当前 published 主配置及有序备用行；
2. 重新校验配置存在、能力一致、成功测试和价格完整；
3. 返回主配置、备用配置和 `routing_version`；
4. 路由不完整时拒绝新作业，不影响手动功能或既有作业。

四类接单事务继续先完成权限、输入和账号活动作业检查，再：

- 使用主配置生成首个请求体；
- 对路由中每个不同配置按其模型、最大输出 Token 和价格计算预留估算；
- 要求所有估算币种与当天额度一致，取 `reserved_nanos` 最大者；
- 创建作业及三条 `AIJobConfigRoute`；
- 原子创建额度预留和原有业务映射。

路由配置只影响新接单作业。即使管理员随后切换主配置、删除备用关系或回滚，已创建作业仍按快照
执行。配置版本本身继续不可变且因作业/路由外键不能物理删除。

## 6. Worker 与失败切换

`claim_next_job()` 在同一事务：

1. 处理超时/尝试耗尽和过期 lease；
2. 将 `attempt_count` 增加 1；
3. 锁定对应 `AIJobConfigRoute`；
4. 用该配置 ID 创建 `AIJobAttempt`；
5. 返回包含实际 `config_version_id` 的 claim。

截图、对话、规划和复盘 loader 均按 claim 配置构造 provider 请求。现有
`reject_or_retry_*()` 决定是否重试：

- 只有 provider 标记 `retryable=true` 且仍有尝试/截止时间时重新排队；
- 下一次 claim 自动选择下一条快照路由；
- 401/403、端点策略、配置解密、结构校验、空输出和业务校验失败保持终态，不切换备用；
- 网络、429、5xx、可重试断流切换；
- lease 过期后的新 attempt 也进入下一条路由，旧 worker 的晚到结果仍由 lease 拒绝。

对话/规划流在新 attempt 开始时沿用 generation reset，先清空前一次部分输出，再显示备用尝试的
新流。所有 attempt 共享 `AIJob.deadline_at`，provider 单次 timeout 继续取
`min(config.timeout_seconds, remaining)`。

## 7. 成本、日志与可观测性

- `AIJobAttempt.config_version_id` 是实际尝试配置事实。
- `AIProviderCost.config_version_id` 固化计价版本；失败和成功尝试分别按各自价格计算平台成本。
- 用户账本仍只引用最终成功 provider cost；失败/取消释放预留，成功收费不超过接单预留，超额由
  平台承担。
- `AIBodyLog` 通过 `(job_id, attempt_number)` 关联 attempt；后台正文详情和成本列表返回实际
  config ID、版本和模型，不返回 Key。
- worker 日志只记录作业、能力、attempt、config ID 和稳定错误码，不记录端点查询、Key 或正文。
- 管理审计记录备用路由变更前后配置 ID 顺序、routing version 和操作者。

## 8. 管理 API

新增：

```text
GET /api/v1/admin/ai-configs/routing/{capability}
PUT /api/v1/admin/ai-configs/routing/{capability}
```

GET 返回：

```json
{
  "capability": "conversation",
  "routing_version": 3,
  "primary": {"id": "uuid", "version": 5, "model": "primary-model"},
  "fallbacks": [
    {"position": 1, "config": {"id": "uuid", "version": 4, "model": "backup"}}
  ]
}
```

PUT 请求：

```json
{
  "expected_routing_version": 3,
  "fallback_config_ids": ["uuid"]
}
```

列表最多两个且不得重复。成功原子替换全部备用行并递增 routing version；完全相同的顺序视为
无变化，不增版。陈旧版本返回 409 和当前 routing version；跨能力、未测试、当前主配置、价格
不完整或币种不一致分别返回稳定错误。

## 9. Web 后台

`AdminAISettings.vue` 在配置版本列表前增加“调用顺序”：

- 只读显示当前主配置；
- 两个有序下拉框选择备用 1/备用 2，选项仅含当前能力可用版本；
- 同一配置不能重复选择，备用 2 可为空；
- 保存按钮显示明确成功、失败和版本冲突恢复；
- 发布新主配置后重读路由，已成为主配置的备用项自动消失；
- 每个版本行显示“主服务”“备用 1”“备用 2”标签。

后台不展示 Key，也不提供绕过合成测试的“强制备用”。桌面、平板、手机和 320px 大字号下下拉框
与操作按钮换行，保持键盘可达和清晰焦点。

## 10. 失败与容量边界

- 无主配置：现有 `ai_capability_unavailable`。
- 备用配置失效或跨能力：保存路由失败；已冻结作业仍可读取其不可变配置。
- 路由保存与主配置发布并发：能力行锁串行，陈旧 `routing_version` 返回 409。
- 接单时路由价格币种不一致：不创建作业、不预留，返回 `ai_quota_pricing_unavailable`。
- 某次可重试失败：记录该 attempt 正文日志和成本，再按原 backoff 重新排队。
- 总截止时间不足：不领取下一次 provider 调用，终态 `timed_out` 并释放额度。
- 全部尝试失败：用户不扣额；每次已发送请求的成本均进入平台成本。
- 路由最多主配置加两个备用，作业最多五次尝试；当前产品固定三次，避免无界故障放大。

## 11. 验证要求

服务端：

- 迁移回填、组合外键、位置/重复约束、级联和安全降级。
- 路由 GET/PUT、测试资格、币种、主配置发布、并发版本及审计。
- 四类作业接单均冻结 `[主, 备用 1, 备用 2]`；无备用保持 `[主, 主, 主]`。
- retryable 故障切换，非 retryable 不切换，总截止时间不重置，lease 晚到结果无效。
- attempt/body log/provider cost 显示实际配置；失败成本归平台，成功只结算一次。
- 最坏预留、实际低价/高价成功、超预留和跨币种拒绝。
- 账号注销级联、旧作业不受路由修改、双 worker 和操作重放。

Web：

- 创建并测试三个同能力配置，发布主配置，保存/重排/清空备用；
- 版本冲突后重读并保留当前选择；
- 真实用户作业首个上游 429/5xx，下一 attempt 到备用并成功；
- 后台成本页显示失败主服务和成功备用服务的不同配置；
- desktop、mobile、tablet、320px 大字号、键盘、axe、控制台和横向溢出。

## 12. 实现状态

当前为已确认实现设计，代码和动态验证尚未完成。完成后按实际结果更新本节及需求、测试和验收
文档。
