# iTime 订阅 Occurrence 单次例外设计

版本：0.1；更新：2026-10-04。本文冻结 ICS 订阅 occurrence 的用户单次取消、移动、内容覆盖及
重置合同，补齐 [时间、课表与重复规则设计](iTime-时间课表与重复规则设计.md) 和
[ICS 订阅设计](iTime-ICS订阅设计.md) 已确认但尚未实现的本地例外层。

## 1. 目标与非目标

目标：

- 订阅来源继续只读，用户只修改某一次投影，不回写或改写来源主事件、RECURRENCE-ID 修订。
- 同一次来源 occurrence 最多有一个本地例外，支持 `cancelled / moved / overridden`。
- 来源刷新、来源字段覆盖和用户单次例外按固定顺序合并，后续刷新不丢失本地例外。
- 用户可删除本地例外并真正恢复当前来源投影，不用伪造一个内容覆盖来模拟恢复。
- 移动后的 occurrence 即使原日期不在查询范围，也能按新日期进入统一日历和冲突计算。
- 写操作具备账号隔离、操作幂等、乐观并发、稳定错误和响应未知后的安全重试。
- 规划输入、同步、本人导出和账号注销覆盖新对象。

非目标：

- 不提供“本次及以后”修改订阅来源；外部重复规则仍由来源刷新维护。
- 不为订阅 occurrence 创建补课；需要额外安排时创建普通固定安排。
- 不把本地例外写回 ICS，不提供 CalDAV 双向同步。
- 不改变订阅字段级覆盖的含义。字段级覆盖仍作用于一个来源事件，本设计只处理一个 occurrence。
- 不因用户主动修改 occurrence 自动重排已确认计划；规划仍由用户显式生成或手工保存新版本。

## 2. 方案选择

评估三种方案：

1. **独立 `CalendarSubscriptionException`（采用）**：以订阅主事件和来源 occurrence key 唯一
   定位，保持完整数据库外键、来源只读和现有投影边界。代价是增加一张表和一组专用 API。
2. 把 `CalendarException` 改为多态外键：表面上复用代码，但 `series_id` 与订阅事件二选一会失去
   直接组合外键，已有拆分、补课、系列版本和迁移语义都需分支，不采用。
3. 为本地修改物化影子 `CalendarSeries`：可复用系列 API，但会复制外部 RRULE、产生双份
   occurrence，并使来源刷新合并和取消语义不再唯一，不采用。

订阅例外使用独立模型，但复用既有本地时间解析、DST 校验、冲突链接和 UI 表单。这样共享领域
规则，而不混合两个生命周期不同的聚合。

## 3. 稳定身份与投影顺序

### 3.1 身份

订阅 occurrence 的稳定身份由以下字段组成：

- `master_event_id`：`recurrence_id=''` 的订阅主事件 ID；
- `source_occurrence_key`：现有投影的 `DATE:... / FLOATING:... / INSTANT:...` key。

`subscription_event_id` 继续表示当前实际提供来源字段的事件，可能是主事件，也可能是
RECURRENCE-ID 修订。投影新增 `subscription_master_event_id`，单次例外只绑定主事件，避免来源
修订出现、消失时改变本地例外身份。

接口继续返回现有完整 `occurrence_key=subscription:{master_event_id}:{source_occurrence_key}`，
但数据库只保存无前缀的 `source_occurrence_key`。来源主事件的 UID 不进入新表、同步事件或错误
详情。

### 3.2 合并顺序

每个 occurrence 按以下顺序得到最终投影：

1. 来源主事件；
2. 来源 RECURRENCE-ID 移动、覆盖或取消；
3. 主事件或 RECURRENCE-ID 上的字段级本地覆盖；
4. 用户单次例外。

本地单次例外只覆盖其动作负责的维度：

- `cancelled`：最终状态为 `cancelled`，`busy=false`；
- `moved`：最终使用本地保存的新时段，并恢复为 `scheduled`。即使来源随后取消该次，用户明确
  保留的本地移动仍存在；界面同时显示“来源已取消，本地移动仍保留”；
- `overridden`：只覆盖明确保存的名称、说明、地点和占用属性，不改变来源取消状态。

投影增加 `source_status`，取 `scheduled / source_cancelled`，用于在最终 `status` 被本地移动
覆盖时仍说明来源事实。无本地移动时，`status` 与现有来源状态一致。

字段级本地覆盖与单次内容覆盖同时存在时，单次内容覆盖优先。删除单次例外后，立即恢复当前来源
修订和字段级覆盖组合出的结果。

## 4. 持久模型

新增 `CalendarSubscriptionException`：

| 字段 | 语义 |
| --- | --- |
| `id / owner_id` | 稳定 ID 与本人归属 |
| `master_event_id` | 订阅主事件组合外键 |
| `source_occurrence_key` | 来源 occurrence 稳定 key |
| `original_start_at / original_end_at` | 创建时原 occurrence 的 UTC 半开区间，用于有界查询 |
| `original_all_day` | 创建时原 occurrence 是否为全天 |
| `action` | `cancelled / moved / overridden` |
| `moved_all_day` | 移动结果是否为全天 |
| `moved_start_date / moved_end_date` | 本地开始日期与排他结束日期 |
| `moved_starts_at / moved_ends_at` | 定时移动的墙上时刻；全天移动为 null |
| `moved_time_zone` | 创建移动时使用的 IANA 时区 |
| `moved_ambiguous_offset_minutes` | 夏令时重复时刻的明确 UTC offset |
| `moved_start_at / moved_end_at` | 校验后固化的 UTC 半开区间，用于冲突和移入范围查询 |
| `override_*` | 单次名称、说明、地点和占用覆盖 |
| `version` | 例外自身乐观版本 |
| `created_at / updated_at` | 审计时间 |

约束：

- `(master_event_id, source_occurrence_key)` 唯一；
- `master_event_id + owner_id` 组合外键指向订阅事件并级联删除；
- 服务层要求目标事件是同一来源的主事件；RECURRENCE-ID 事件不可直接成为例外父对象；
- `moved` 必须有完整日期、时区和全天/定时一致字段；
- 全天移动要求 `moved_end_date > moved_start_date`，最多 366 天，时间与 offset 均为空；
- 定时移动要求开始/结束时刻完整，结束日期只能是开始当天或次日，并执行 DST 缺失/重复校验；
- 非 `moved` 动作的移动字段全部为空；
- `overridden` 至少有一个覆盖字段，名称非空；其他动作不携带覆盖字段；
- `version >= 1`，key 非空且最长 512 字符。
- 原始 UTC 区间始终完整且结束晚于开始；移动 UTC 区间只在 `moved` 时存在并保持同样约束。

索引覆盖：

- `owner_id + master_event_id + source_occurrence_key`；
- `owner_id + original_start_at + master_event_id`；
- `owner_id + moved_start_at + master_event_id` 的 `action='moved'` 部分索引，用于移入范围查询。

迁移同时：

- 为新表建立 `bump_planning_input_revision()` 触发器；
- 例外变化递增 `CalendarSource.version` 并复用 `calendar_source` 无正文失效事件，与既有订阅字段
  覆盖保持同一同步聚合边界；
- 回滚前若存在订阅例外则拒绝，避免静默丢失用户修改；空表回滚时清理对应幂等结果和 change
  event。

## 5. API 合同

新增：

```text
POST   /api/v1/calendar/subscriptions/{source_id}/exceptions
PUT    /api/v1/calendar/subscriptions/{source_id}/exceptions/{exception_id}
DELETE /api/v1/calendar/subscriptions/{source_id}/exceptions/{exception_id}
```

创建请求包含：

```json
{
  "operation_id": "uuid",
  "expected_source_version": 4,
  "master_event_id": "uuid",
  "source_occurrence_key": "INSTANT:2026-10-06T09:00:00Z",
  "action": "cancelled"
}
```

更新请求包含 `expected_source_version`、`expected_version` 和完整目标动作字段。删除请求包含
`operation_id`、`expected_source_version`、`expected_version`。

服务端写入顺序：

1. 重放相同账号、scope 和 operation ID 的既有结果；
2. 锁定 `CalendarSource`，校验本人来源及 `expected_source_version`；
3. 锁定并解析主事件，验证 occurrence key 确实属于当前来源规则或现有本地例外；
4. 创建、更新或删除例外；
5. 来源版本递增，写 `calendar_source` 无正文同步事件；
6. flush 后按最终投影返回该 occurrence 的冲突。

不存在和跨账号统一返回 404。稳定错误包括：

- `calendar_subscription_occurrence_not_found`：当前来源规则中不存在该 occurrence；
- `calendar_subscription_exception_exists`：该次已有例外，应改用更新；
- `calendar_subscription_exception_not_found`：例外不存在或不属于该来源；
- `calendar_subscription_version_conflict`：来源或刷新已变化；
- `calendar_subscription_exception_version_conflict`：例外被其他设备修改；
- `invalid_local_time / ambiguous_local_time`：移动时段无法安全解析。

幂等重放返回首次提交的完整响应。相同 operation ID 改变请求内容继续返回
`operation_id_reused`。数据库结果未知时，客户端保留 operation ID 和填写内容供原样重试。

## 6. 查询、移动与容量

`query_subscription_occurrences()` 先展开来源，再按有界条件加载订阅例外：

- 正常展开最多得到 2000 个 `(master_event_id, source_occurrence_key)`，再按这些组合键精确查询
  例外，不扫描账号全部历史例外；
- 额外按 `moved_start_at < query_end AND moved_end_at > query_start` 查询移入范围的例外；
- 对移入范围的例外，从 key 还原原始日期/时刻，并用当前 RRULE/RDATE/EXDATE 验证它仍是来源
  occurrence；同一结果只输出一次；
- 来源规则暂时不再包含该 key 时，例外保留为 dormant，不自行生成孤立日程；若后续来源再次
  出现该 key，例外自动恢复生效；
- 来源主事件被整体取消时仍按最后成功基线投影历史和未来；本地 `moved` 可按第 3.2 节继续保留
  最终 occurrence。

查询仍遵守账号最多 5000 条订阅事件、单次日历最多 2000 条 occurrence 和 93 天日期范围。
例外数量不另设静默截断；查询到超过剩余上限时返回 `calendar_result_too_large`。

冲突继续使用半开区间 `[start,end)`。本地取消和 `busy=false` 不参与冲突；本地移动使用新时段；
全天事件按本地排他结束日期转换为 UTC 后参与冲突。写响应最多返回现有 100 条冲突并标记
`has_more`。

## 7. Web 交互

统一日历详情对订阅 occurrence 提供：

- 未取消时：“取消本次”“调整本次时间”“覆盖本次内容”；
- 已有本地例外时：“重置为来源”，删除例外并恢复当前来源投影；
- 始终保留“管理来源与字段覆盖”，进入现有订阅管理页；
- 不显示“编辑整个系列”“修改本次及以后”或“安排补课”。

移动表单：

- 定时 occurrence 使用日期、开始、结束、跨日和当前显示时区，重复时刻要求用户选 UTC offset；
- 全天 occurrence 使用开始和排他结束日期，不显示无意义的时刻；默认保持原天数；
- 保存期间禁用重复提交；失败保留填写，显示保存状态、恢复动作和 request ID。

详情同时展示：

- “本地单次修改：已取消 / 已移动 / 已覆盖内容”；
- 来源取消但本地移动保留时的明确说明；
- 当前冲突提示与冲突数量。

桌面详情栏和移动单栏复用同一组件。按钮使用现有 Lucide 图标和命令样式；320px 最大字号下表单
换行，不出现横向溢出、遮挡或不可达控件。

## 8. 规划、同步、导出与注销

- 新表的 INSERT/UPDATE/DELETE 触发 planning input revision，使后续提案读取最终本地投影。
- 用户主动修改不会创建“来源刷新冲突”记录；若已有开放来源冲突，其历史仍保留，用户可在规划页
  明确保留或重新生成。
- 每次实际创建、更新、删除递增来源版本并发出 `calendar_source` 事件；无变化更新不增版、不发
  事件。
- 本人 JSON 导出在 `calendar.subscription_exceptions` 保存完整字段；ICS 导出把来源规则、本地字段
  覆盖和单次例外合成最终 VEVENT/RECURRENCE-ID，取消写 CANCELLED 修订，移动和内容覆盖写最终
  RECURRENCE-ID 项，并跳过被本地例外取代的来源修订。
- CSV 日历分类增加 `kind=subscription_exception` 数据行，保留可恢复的动作、来源 key 和本地字段，
  不泄露订阅 URL 查询参数、内部 UID、租约或操作去重内容。
- 删除订阅来源或账号时通过组合外键级联删除例外；账号注销完成后显式测试 owner 行为 0，删除
  回执不保留例外正文。

## 9. 并发与失败恢复

- 订阅刷新和例外写入都先锁 `CalendarSource`，再锁事件/例外，避免死锁和半应用来源。
- 来源在用户编辑期间刷新：保存返回 409；界面重读当前来源，但保留用户输入供明确重试。
- 两个设备同时创建同一次例外：一个成功，另一个返回已有例外 ID 和版本。
- 更新与重置竞争：只允许一个匹配 `expected_version` 的事务成功。
- 刷新事务不会更新、删除或重建本地例外；只改变来源投影。
- 写响应未知：同 operation ID 原样重试；不能生成第二条例外或重复版本。
- 冲突检查容量超限：例外写入不提交，返回可恢复 422，避免保存成功却声称无冲突。

## 10. 验证要求

服务端：

- 迁移升级/降级、全部 CHECK、唯一键、组合所有权外键、来源和账号级联。
- 单次取消、定时/全天移动、内容覆盖、删除恢复、无变化更新和操作重放。
- DST 缺失/重复、跨午夜、移入/移出查询范围、首尾相接和实际重叠。
- 来源 RID 移动/取消、主事件刷新、来源整体取消、字段覆盖叠加、dormant 后恢复。
- 来源刷新与创建并发、双创建、更新与删除并发、跨账号 404。
- planning revision、change feed、JSON/CSV/ICS 导出、来源删除和账号注销。

Web：

- 真实注册、受控 ICS worker 刷新、日历进入 occurrence 后完成取消、恢复、移动和内容覆盖。
- 来源刷新后本地例外仍生效；来源取消与本地移动同时存在时说明准确。
- 版本冲突重读并保留草稿，未知响应使用同 operation ID 重试。
- 桌面、平板、手机和 320px 最大字号深色；键盘、axe、控制台、API 500、焦点恢复和根级横向
  溢出。

## 11. 实现状态

迁移 `0032_subscription_exceptions`、`calendar/subscription_exception_service.py`、订阅投影和
写 API 已实现独立例外模型、两阶段有界查询、来源版本并发控制、planning revision、同步、本人
JSON/CSV/ICS 导出及账号级联。`CalendarOccurrencePanel.vue` 已提供订阅单次取消、重置、定时/
全天移动、内容覆盖和来源管理入口；来源取消后明确显示本地移动仍保留。

服务端专项覆盖约束、DST、移入范围、RID、刷新保留、来源取消、跨账号、并发、幂等、冲突、
导出和注销；Chromium 覆盖 desktop、mobile、tablet 以及 320px 深色大字号。实际命令和结果见
[Web 实施与验证](../验收/iTime-Web实施与验证.md)。
