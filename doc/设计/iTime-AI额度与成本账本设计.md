# iTime AI 额度与成本账本设计

版本：0.2；更新：2026-10-03。本文定义 AI 供应商用量、不可变价格快照、逐尝试平台成本和用户额度账本。价格版本、用量捕获、逐尝试平台成本及管理查询已实现；用户已授权 goal 模式下由代理自行决定并持续开发，额度采用“结果校验可查看即结算”的完整方案。

## 1. 范围与分账原则

平台供应商成本与用户权益金额分开记录：

- `ProviderCost` 回答“某次真实上游尝试消耗了多少、按哪个价格计算”，失败、重试和最终成功尝试都分别保留。
- `Reservation` 回答“某个用户作业暂时占用了多少当日权益”。
- `UsageLedger` 回答“某个用户作业最终扣除或返还了多少权益”。
- `DailyAllowance` 保存某用户某活动日的额度配置、调整和可推导余额。

平台成本事实不代表用户应被扣额。失败、取消和失败重试可产生平台成本，但按已确认规则不得扣用户额度。质量样本、正文日志和 SSE 消费也不触发结算。

已交付：

1. AI 配置版本内的价格快照。
2. 对话与截图 provider 的 usage 兼容解析。
3. 每个实际 worker attempt 的平台成本事实。
4. 真实 PostgreSQL、HTTP、重试、取消和租约竞态测试。

下一阶段交付：

- 后台版本化每日额度策略、用户单日调整与审计。
- 接单活动日冻结、原子预留、成功结算和失败/取消释放。
- 用户余额、活动预留和结算账本页面。
- 后台额度策略、用户调整和用户账本页面。

充值、支付、订单或现金钱包仍不属于首版。

## 2. 方案选择

### 2.1 采用：价格随 AI 配置版本冻结

`AIConfigVersion` 已同时冻结能力、端点、模型、Key、超时和最大输出量，作业也已绑定该版本。价格继续作为同一不可变版本的组成部分，避免端点版本与价格版本错配；配置 UUID 和业务版本号就是价格版本标识。

新增价格字段：

- `price_currency`：三个大写 ASCII 字母；首版不假定固定币种。
- `input_price_nanos_per_million_tokens`。
- `cached_input_price_nanos_per_million_tokens`。
- `output_price_nanos_per_million_tokens`。

金额以一币种单位的十亿分之一为最小存储单位，所有计算使用整数。后台 API 和 UI 使用每百万 Token 的十进制定价字符串，服务端精确转换为纳币种单位，不接受二进制浮点计算。缓存输入价格未单独填写时，在创建版本时物化为普通输入价格。

新建配置必须明确价格；迁移前已经存在的版本保留 `NULL` 价格并显示“未配置价格”，不得伪造零成本。分阶段升级期间这些版本仍可执行，但只能产生“未定价”的 usage 事实；用户额度启用前必须发布带价格的新版本。

### 2.2 未采用：独立价格目录

独立 `PriceVersion` 能让同一端点配置复用多套价格，但会引入配置发布与价格发布的双指针、在途作业双版本绑定和回滚组合。当前每项能力只绑定一个平台选择的模型，收益不足以抵消一致性复杂度。

### 2.3 拒绝：结算时读取当前价格

按当前价格回算历史 usage 会使同一作业随后台改价而变化，也无法解释重试成本。价格必须在接单配置版本和逐尝试成本记录中冻结。

## 3. Provider usage 合同

标准化字段均为非负整数或 `NULL`：

- `input_tokens`
- `cached_input_tokens`
- `output_tokens`
- `total_tokens`

兼容以下 OpenAI 风格别名：

- `prompt_tokens` / `completion_tokens` / `total_tokens`
- `input_tokens` / `output_tokens` / `total_tokens`
- `prompt_tokens_details.cached_tokens`
- `input_tokens_details.cached_tokens`

`cached_input_tokens` 不得大于 `input_tokens`。供应商的 `total_tokens` 不强制等于输入加输出；成本只使用普通输入、缓存输入和输出三个已知分量。保留经过深度、键名、数值和体积限制的计量详情，不保存任意字符串、正文或供应商错误体。

usage 有三种来源状态，计价状态与其分开：

| 状态 | 含义 |
| --- | --- |
| `provider` | 上游返回了可用的标准化计数 |
| `estimated` | 上游缺失或返回非法 usage，使用版本化本地算法估算 |
| `not_sent` | 尝试在真正发出上游请求前结束，Token 和成本为零 |

当 usage 可用或可估算、但绑定的旧配置没有价格时，来源仍为 `provider` 或 `estimated`，金额状态为“未定价”且金额保持 `NULL`。

无效或缺失 usage 不能使已经校验通过的业务结果失败，也不能触发第二次模型调用。对话流请求发送 `stream_options.include_usage=true`，并接受 `[DONE]` 前 `choices=[]` 的 usage-only 事件；一次性 JSON 和截图响应读取顶层 usage。

## 4. 逐尝试成本事实

`AIProviderCost` 与 `(job_id, attempt_number)` 一对一，关联所有者、能力、配置版本和真实 attempt。记录：

- attempt 终态与 `provider_request_started_at`。
- 标准化 Token 数、usage 来源、估算原因和估算算法版本。
- 价格币种及三个价格分量的不可变副本。
- 输入、缓存输入、输出和总成本纳币种单位。
- 创建时间；记录一旦插入不得更新。

成本计算：

```text
regular_input = input_tokens - cached_input_tokens
numerator =
    regular_input * input_price
  + cached_input_tokens * cached_input_price
  + output_tokens * output_price
total_cost_nanos = ceil(numerator / 1_000_000)
```

先汇总全部分量再向上取整，避免逐分量舍入放大。所有乘法在应用层检查 `BIGINT` 上限，数据库同时约束非负值和总额一致性。

### 4.1 请求是否已经发出

worker 在端点策略、DNS 和密钥解密完成后、真正开始 HTTP 请求前，以当前租约事务写入 `AIJobAttempt.provider_request_started_at`。回调发现租约失效时不得发送请求。

- 未写入该时间的配置、媒体、端点和排队期限失败为 `not_sent`。
- 已写入但没有有效 usage 的网络错误、取消或租约失效为 `estimated`。
- 取消或旧租约先完成成本事实后，晚到 usage 不得更新记录。

### 4.2 估算

估算算法带固定版本：

- 文本按 UTF-8 字节和消息结构的保守规则估算输入 Token。
- 截图按已冻结宽高、`detail=high` 及提示词估算图像与文本输入。
- 已持久化响应片段用于估算输出；没有响应片段时记录零输出并标明估算原因，不能宣称为供应商实际值。

估算是对账辅助，不冒充供应商账单。后续取得供应商账单时通过独立对账记录表达差异，不修改原始 attempt 成本事实。

## 5. 事务与竞态

成本事实必须在已有 attempt 终态事务中创建：

- 成功：消息/识别结果、attempt、作业、正文日志、质量采样和成本事实原子提交。
- 可重试失败：当前 attempt 成本落账后再把作业放回队列；新 attempt 有独立成本。
- 最终失败或超时：成本事实与作业终态原子提交。
- 取消：取消事务锁住作业，结束当前 attempt 并写成本；晚到结果和 usage 均被租约门禁拒绝。
- 租约失效：新 worker 领取前先把旧 attempt 标为 `abandoned` 并写成本，再创建新 attempt。
- 排队超时或尚未产生 attempt：不创建虚假的供应商成本。

唯一约束和 attempt 状态锁保证每次尝试至多一条成本事实。写成本失败必须回滚整个终态事务，不能留下“作业成功但成本丢失”的状态。

## 6. 活动日、预留与成功结算

已有 `activity_day_at()` 按用户 IANA 时区和当地 04:00 计算活动日。对话和截图接单必须冻结接单时的时间点、用户时区和边界分钟，不能在结算时使用用户后来修改的时区。

采用以下结算规则：

1. 在接单事务锁定时间设置和对应活动日额度。
2. 按冻结输入和配置最大输出量计算保守预算；余额足够才创建与作业唯一关联的预留。重试共享同一预留，不追加占用。
3. 结果校验通过且可供用户查看时，以最终成功 attempt 的 provider cost 结算；业务提案在可预览时已完成结算，后续拒绝、过期或应用冲突不返还。
4. 失败、超时和取消释放全部预留；失败重试的 provider cost 只计平台成本。
5. 实际成功成本低于预留时扣实际值并释放余量；高于预留时最多扣预留值，超出部分由平台承担。
6. 作业终态后到达的供应商 usage 不追改用户账本；终态时缺失 usage 使用已标记估算值。
7. 跨 04:00 或接单后修改时区均继续归属接单时冻结的活动日。
8. AI 冻结只阻止新接单；已经持有预留的在途作业继续到终态并按上述规则结算或释放。
9. 操作号重放返回原预留，不重新计算日期。

缺失用户时间设置不得静默使用设备时间；额度启用时返回明确的 `time_settings_required`，由客户端保存浏览器 IANA 时区后重试。

### 6.1 策略、每日额度与账本

- `AIQuotaPolicyVersion` 保存币种和每日额度纳单位；草稿发布后只影响首次创建的新活动日额度，既有活动日不被改写。
- `AIDailyAllowance` 冻结策略版本、基础额度、活动日、时区和对应 UTC `[window_start_at, window_end_at)`；用户修改时区后，只要当前时刻仍落在既有窗口内就复用该窗口，避免切换时区重复刷新。
- `AIQuotaReservation` 与作业一对一，状态为 `reserved / settled / released`，保存预留、最终成本、实际扣额和释放原因。
- `AIQuotaLedgerEntry` 保存成功扣额和管理员增减，金额为带方向整数；成功作业每个 reservation 至多一条扣额。
- 可用额度由基础额度、管理员调整、成功扣额和活动预留推导。客户端不能提交余额或扣额。
- `AIJob.quota_mode` 区分迁移前由平台承担的历史作业与启用后的用户额度作业；只有用户额度作业必须在同一事务创建 reservation。

接单按 `User → Conversation/TimeSettings → DailyAllowance → 新建 Job/Reservation`，终态按 `AIJob → 业务投影/Attempt/ProviderCost → Reservation → DailyAllowance → Ledger`，管理调整按 `管理员 → 目标 User → DailyAllowance → Adjustment/Ledger` 加锁。任何路径都不得先锁额度再回头锁既有作业。接单、作业创建和预留同一事务；成功结果、provider cost、结算账本和业务结果同一事务；失败/取消终态与释放同一事务。

预留估算使用独立版本：冻结完整 provider 请求，输入按保守 UTF-8/图像规则估算，输出按配置 `max_output_tokens` 计价。现有终态 usage 估算器不能直接当作最大预算。未发布额度策略、AI 配置未定价或币种不一致时拒绝新接单，不创建消息、媒体、作业或预留。

## 7. API 与界面

后台 AI 配置创建、详情和列表增加：

- 币种。
- 每百万普通输入 Token 价格。
- 每百万缓存输入 Token 价格。
- 每百万输出 Token 价格。
- 是否已配置价格。

Key 仍只返回已配置状态。价格字段属于不可变版本；修改价格需要创建、测试并发布新版本。配置历史显示价格和币种，避免管理员误把旧价格用于新作业。

平台成本查询将在额度后台功能中提供，默认只返回元数据和聚合：

- 时间、能力、模型、配置版本、attempt 状态。
- usage 来源、Token 分量、成本和币种。
- 作业、请求 ID 和错误阶段等排障关联。

普通用户页面只展示自己的额度、预留和扣额，不展示平台失败重试成本或供应商内部请求信息。

## 8. 测试与验收

服务和数据库测试至少覆盖：

- 价格字符串精确转换、九位小数上限、负数/溢出/非法币种。
- 价格随配置版本不可变，发布和回滚不改写历史成本。
- 流式 usage-only 块、拆帧、一次性 JSON、截图 usage 和字段别名。
- usage 缺失、非法和 `cached_input_tokens > input_tokens` 时结果仍成功但标为估算。
- 成功、429 后重试、校验失败、超时、取消和租约失效各自产生正确 attempt 成本。
- 取消或旧租约后的晚到 usage 不能覆盖已提交事实。
- 排队超时、端点策略失败等未发送尝试为零成本；没有 attempt 时不造记录。
- 双 worker、重复终态提交和数据库回滚下至多一条成本事实。
- 迁移升级、带历史未定价配置升级、模型漂移检查和有数据降级保护。

真实 HTTP 桩必须断言对话请求包含 `stream_options.include_usage=true`。真实 PostgreSQL 断言逐尝试行、外键、唯一键、金额计算和终态原子性；不能只测试纯函数或静态 schema。

## 9. 用户与后台 API 准入

- 用户 `GET /api/v1/quota` 返回当前活动日、基础额度、调整、已扣、预留和可用金额。
- 用户 `GET /api/v1/quota/ledger` 返回本人成功扣额和管理员调整，明确 provider 上报或估算来源。
- 后台额度策略使用草稿/发布/历史版本；发布前检查当前 AI 配置都有价格且币种一致。
- 后台用户调整必须带 operation ID、目标活动日、原因和带方向金额；重复请求返回原结果，不允许把已用加预留后的可用金额调成负数。
- 后台账本可按用户、活动日、类型和币种筛选；普通用户不可读取平台失败尝试成本。

当前实现由迁移 `0019_ai_provider_costs`、`0020_ai_provider_cost_lifecycle`，`server/src/itime/ai/{pricing,usage,cost_service}.py`、两类 provider/worker 终态接入，以及后台 `GET /api/v1/admin/provider-costs` 和 Web `/admin/costs` 构成。供应商 usage 详情只保留白名单计数，不永久复制未知键名或正文。
