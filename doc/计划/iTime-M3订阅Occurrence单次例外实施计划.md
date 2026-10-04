# iTime M3 订阅 Occurrence 单次例外实施计划

当前进度：全部任务已实现、验证并提交；实际命令与结果见
[Web 实施与验证](../验收/iTime-Web实施与验证.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 ICS 订阅 occurrence 提供可持久、可同步、可重置的用户单次取消、移动和内容覆盖，
并保证来源刷新后本地例外仍按最终优先级投影。

**Architecture:** 新增独立 `CalendarSubscriptionException`，以订阅主事件和稳定来源 occurrence key
唯一定位。订阅投影先组合来源主事件、RECURRENCE-ID 和字段覆盖，再应用单次例外；写 API 以
`CalendarSource` 版本串行化来源刷新和用户修改。规划 revision、本人导出和账号删除复用数据库
触发器与组合外键，不建立影子系列。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、PostgreSQL 18、Alembic、Vue 3、
TypeScript、Lucide、Playwright Chromium、axe。

---

## 文件结构

服务端模型与迁移：

- `server/src/itime/migrations/versions/0032_calendar_subscription_exceptions.py`：例外表、约束、
  索引、planning revision 触发器和安全回滚。
- `server/src/itime/calendar/models.py`：`CalendarSubscriptionException` ORM。
- `server/tests/test_calendar_subscription_exception_models.py`：真实 PostgreSQL 约束、所有权、
  级联和迁移往返。
- `server/tests/test_planning_models.py`：planning revision 触发表集合。

服务端领域与 API：

- `server/src/itime/calendar/subscription_exception_service.py`：稳定身份解析、输入校验、例外应用、
  序列化和写后冲突读取。
- `server/src/itime/calendar/subscription_schemas.py`：创建、更新和重置输入。
- `server/src/itime/calendar/subscription_projection.py`：有界加载、正常应用和移入范围投影。
- `server/src/itime/calendar/subscription_router.py`：本人例外写 API、幂等和并发控制。
- `server/tests/test_calendar_subscription_exceptions.py`：真实 HTTP/PostgreSQL/worker 业务测试。
- `server/tests/test_calendar_subscriptions.py`、`test_plan_source_conflicts.py`：刷新和计划冲突回归。

生命周期：

- `server/src/itime/data_export/snapshot.py`：JSON 快照白名单。
- `server/src/itime/data_export/archive.py`：CSV 行及最终 ICS RECURRENCE-ID。
- `server/tests/test_data_export_snapshot.py`、`test_data_export_archive.py`：导出内容和敏感字段排除。
- `server/tests/test_sync_event_coverage.py`：无正文同步事件。
- `server/tests/test_account_deletion_worker.py`：账号删除级联。

Web：

- `web/src/lib/calendar.ts`：occurrence 新字段、订阅例外类型和写 API。
- `web/src/components/CalendarOccurrencePanel.vue`：订阅取消、移动、覆盖和重置交互。
- `web/src/pages/CalendarPage.vue`：同步失效类型和刷新后的焦点恢复。
- `web/src/calendar.css`：全天/定时表单及来源状态提示。
- `web/e2e/calendar-subscriptions.spec.ts`：真实受控 ICS、worker 和多视口流程。

文档：

- `README.md`、`doc/README.md`、需求追踪、详细设计、时间课表设计、ICS 订阅设计、测试方案、
  验收清单、Web 实施记录及本计划。

## Task 1：建立订阅例外持久模型

**Files:**
- Create: `server/src/itime/migrations/versions/0032_calendar_subscription_exceptions.py`
- Modify: `server/src/itime/calendar/models.py`
- Create: `server/tests/test_calendar_subscription_exception_models.py`
- Modify: `server/tests/test_planning_models.py`

- [x] **Step 1: 写迁移失败测试**

创建 `test_calendar_subscription_exception_models.py`，升级 head 后写入主事件和最小合法取消例外：

```python
exception_id = uuid4()
connection.execute(
    """
    INSERT INTO calendar_subscription_exceptions (
        id, owner_id, master_event_id, source_occurrence_key,
        original_start_at, original_end_at, original_all_day,
        action, version
    ) VALUES (
        %s, %s, %s, 'INSTANT:2026-10-06T09:00:00Z',
        '2026-10-06T09:00:00Z', '2026-10-06T10:00:00Z',
        false, 'cancelled', 1
    )
    """,
    (exception_id, owner_id, master_event_id),
)
```

同文件覆盖：

- 非主事件作为父对象由服务层测试拒绝；
- 非法 action、空 key、原始结束不晚于开始、非法版本；
- `moved` 缺少 UTC 区间、日期、时区或全天/定时字段不一致；
- 非 `moved` 携带移动字段；
- `overridden` 无字段、空名称，其他动作携带覆盖字段；
- 同主事件和 key 重复；
- 跨 owner 组合外键；
- 删除订阅事件和用户后级联为 0；
- 新表 INSERT/UPDATE/DELETE 各使 planning revision 增加一次；
- 有数据时拒绝降级，清空后可降到 `0031_plan_source_conflicts` 再升级。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_calendar_subscription_exception_models.py \
  tests/test_planning_models.py
```

Expected: FAIL，迁移、ORM 和触发表尚不存在。

- [x] **Step 3: 实现迁移和 ORM**

模型字段固定为：

```python
class CalendarSubscriptionException(Base):
    __tablename__ = "calendar_subscription_exceptions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    master_event_id: Mapped[UUID]
    source_occurrence_key: Mapped[str] = mapped_column(String(512))
    original_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    original_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    original_all_day: Mapped[bool] = mapped_column(Boolean)
    action: Mapped[str] = mapped_column(String(12))
    moved_all_day: Mapped[bool | None] = mapped_column(Boolean)
    moved_start_date: Mapped[date | None] = mapped_column(Date)
    moved_end_date: Mapped[date | None] = mapped_column(Date)
    moved_starts_at: Mapped[time | None] = mapped_column(Time(timezone=False))
    moved_ends_at: Mapped[time | None] = mapped_column(Time(timezone=False))
    moved_time_zone: Mapped[str | None] = mapped_column(String(64))
    moved_ambiguous_offset_minutes: Mapped[int | None] = mapped_column(Integer)
    moved_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moved_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    override_name: Mapped[str | None] = mapped_column(String(200))
    override_description: Mapped[str | None] = mapped_column(Text)
    override_location: Mapped[str | None] = mapped_column(String(300))
    override_busy: Mapped[bool | None] = mapped_column(Boolean)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

迁移建立 `(master_event_id, owner_id)` 组合外键、`(master_event_id,
source_occurrence_key)` 唯一键、owner/original 和 owner/moved 部分索引。创建：

```sql
CREATE TRIGGER trg_planning_revision_calendar_subscription_exceptions
AFTER INSERT OR UPDATE OR DELETE ON calendar_subscription_exceptions
FOR EACH ROW EXECUTE FUNCTION bump_planning_input_revision()
```

降级先检查表为空，再删除该触发器、`calendar_subscription_exceptions.%` 幂等结果、索引和表。

- [x] **Step 4: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_calendar_subscription_exception_models.py \
  tests/test_planning_models.py
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run ruff check src/itime/calendar/models.py \
  src/itime/migrations/versions/0032_calendar_subscription_exceptions.py \
  tests/test_calendar_subscription_exception_models.py tests/test_planning_models.py
git add src/itime/calendar/models.py \
  src/itime/migrations/versions/0032_calendar_subscription_exceptions.py \
  tests/test_calendar_subscription_exception_models.py tests/test_planning_models.py
git commit -m "feat: 建立订阅单次例外模型"
```

Expected: 专项测试、迁移 current/check、回滚往返和 Ruff 均通过。

## Task 2：实现例外投影与本人 API

**Files:**
- Create: `server/src/itime/calendar/subscription_exception_service.py`
- Modify: `server/src/itime/calendar/subscription_schemas.py`
- Modify: `server/src/itime/calendar/subscription_projection.py`
- Modify: `server/src/itime/calendar/subscription_router.py`
- Create: `server/tests/test_calendar_subscription_exceptions.py`
- Modify: `server/tests/test_calendar_subscriptions.py`
- Modify: `server/tests/test_plan_source_conflicts.py`

- [x] **Step 1: 写真实 HTTP/worker 失败测试**

测试用现有 `FakeFetcher` 和真实 worker 创建 DAILY、全天及 RECURRENCE-ID 来源，先断言：

```python
created = client.post(
    f"/api/v1/calendar/subscriptions/{source_id}/exceptions",
    json={
        "operation_id": str(operation_id),
        "expected_source_version": occurrence["source_version"],
        "master_event_id": occurrence["subscription_master_event_id"],
        "source_occurrence_key": occurrence["source_occurrence_key"],
        "action": "cancelled",
    },
)
assert created.status_code == 201, created.text
assert created.json()["occurrence"]["status"] == "cancelled"
assert created.json()["occurrence"]["busy"] is False
```

继续覆盖：

- 同 operation ID 同内容重放、异内容 409；
- 同次重复创建返回 `calendar_subscription_exception_exists`；
- 更新为定时移动、跨午夜移动、全天移动和内容覆盖；
- DELETE 真正恢复当前来源值，重复 DELETE 通过 operation replay 返回同一结果；
- DST 缺失拒绝、DST 重复返回 offset 候选并可明确保存；
- 移入查询范围出现、移出原范围消失、首尾相接不冲突；
- 来源 RID 先移动/取消后，本地例外最后应用；
- 来源刷新后本地取消/覆盖/移动保留，来源取消后本地移动仍为 scheduled 且
  `source_status=source_cancelled`；
- 当前规则移除 key 时例外 dormant，再次出现时恢复；
- 跨账号统一 404，来源版本和例外版本冲突为 409；
- 双创建、更新/删除竞争只有一个成功；
- 304、失败刷新不改变例外。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_calendar_subscription_exceptions.py \
  tests/test_calendar_subscriptions.py tests/test_plan_source_conflicts.py
```

Expected: 新接口 404 或新字段缺失；既有用例仍通过。

- [x] **Step 3: 增加输入模型和领域服务**

`subscription_schemas.py` 定义：

```python
class SubscriptionExceptionFields(Input):
    action: Literal["cancelled", "moved", "overridden"]
    moved_all_day: bool | None = None
    moved_start_date: date | None = None
    moved_end_date: date | None = None
    moved_starts_at: time | None = None
    moved_ends_at: time | None = None
    moved_time_zone: str | None = Field(default=None, max_length=64)
    moved_ambiguous_utc_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    override_name: str | None = Field(default=None, max_length=200)
    override_description: str | None = Field(default=None, max_length=20_000)
    override_location: str | None = Field(default=None, max_length=300)
    override_busy: bool | None = None

class SubscriptionExceptionCreate(SubscriptionExceptionFields):
    operation_id: UUID
    expected_source_version: int = Field(ge=1)
    master_event_id: UUID
    source_occurrence_key: str = Field(min_length=6, max_length=512)

class SubscriptionExceptionUpdate(SubscriptionExceptionFields):
    operation_id: UUID
    expected_source_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)

class SubscriptionExceptionDelete(OperationInput):
    expected_source_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)
```

`subscription_exception_service.py` 提供四个明确边界：

- `resolve_source_occurrence(session, owner_id, master, source_occurrence_key) -> dict`：验证稳定 key
  属于当前来源规则并返回已应用 RID 与字段覆盖的来源投影；
- `validate_subscription_exception(data, occurrence, default_time_zone) -> dict`：返回已清洗字段和固化
  UTC 区间；
- `apply_subscription_exception(occurrence, exception) -> dict`：只应用动作负责的最终字段；
- `subscription_exception_result(exception) -> dict`：只返回 API 白名单字段。

`resolve_source_occurrence()` 必须用当前 master 的 RRULE/RDATE/EXDATE 验证 key，并叠加当前 RID 和
字段覆盖。移动输入通过 `resolve_local_interval()` 计算并固化 UTC；全天区间用
`datetime.combine(date, time.min, ZoneInfo(zone)).astimezone(UTC)`。

- [x] **Step 4: 改造订阅投影为两阶段有界加载**

第一阶段产生来源候选并记录：

```python
candidate_keys = {
    (candidate.master.id, candidate.source_occurrence_key)
    for candidate in candidates
}
```

第二阶段使用 SQLAlchemy `tuple_()` 精确查询正常例外，并额外查询移动区间：

```python
normal = select(CalendarSubscriptionException).where(
    CalendarSubscriptionException.owner_id == owner_id,
    tuple_(
        CalendarSubscriptionException.master_event_id,
        CalendarSubscriptionException.source_occurrence_key,
    ).in_(candidate_keys),
)
moved = select(CalendarSubscriptionException).where(
    CalendarSubscriptionException.owner_id == owner_id,
    CalendarSubscriptionException.action == "moved",
    CalendarSubscriptionException.moved_start_at < query_end,
    CalendarSubscriptionException.moved_end_at > query_start,
)
```

应用后输出 `source_version`、`subscription_master_event_id`、`source_status` 及现有
`exception_id/version/action`。移入范围记录先验证 key 仍属于当前来源规则；使用集合去重。

- [x] **Step 5: 实现创建、更新、重置 API**

三个路由都按以下锁序：

```python
source, _subscription = await locked_subscription(
    identity.session,
    identity.user.id,
    source_id,
    for_update=True,
)
if source.version != data.expected_source_version:
    raise subscription_version_conflict(source)
```

创建校验 master 属于来源且 `recurrence_id == ""`，解析 occurrence 后插入。更新和 DELETE 以
`id + owner_id` 锁例外，再验证其 master 仍属于路径来源。实际变化时：

```python
source.version += 1
source.updated_at = current
emit_change(session, owner_id, "calendar_source", source.id, "updated", source.version)
```

实际实现沿用订阅聚合边界，只发出 `calendar_source`；新表触发 planning revision，无需再发一条
重复的 `calendar_exception` 事件。flush 后读取最终 occurrence 和冲突；任何读取/容量错误让
事务整体回滚。

- [x] **Step 6: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_calendar_subscription_exceptions.py \
  tests/test_calendar_subscriptions.py tests/test_calendar_series.py \
  tests/test_calendar_exceptions.py tests/test_calendar_additions.py \
  tests/test_plan_source_conflicts.py tests/test_planning_inputs.py
uv run ruff check src/itime/calendar/subscription_exception_service.py \
  src/itime/calendar/subscription_schemas.py \
  src/itime/calendar/subscription_projection.py \
  src/itime/calendar/subscription_router.py \
  tests/test_calendar_subscription_exceptions.py
git add src/itime/calendar/subscription_exception_service.py \
  src/itime/calendar/subscription_schemas.py \
  src/itime/calendar/subscription_projection.py \
  src/itime/calendar/subscription_router.py \
  tests/test_calendar_subscription_exceptions.py \
  tests/test_calendar_subscriptions.py tests/test_plan_source_conflicts.py
git commit -m "feat: 支持订阅单次例外"
```

## Task 3：接入同步、导出和注销生命周期

**Files:**
- Modify: `server/src/itime/data_export/snapshot.py`
- Modify: `server/src/itime/data_export/archive.py`
- Modify: `server/tests/test_data_export_snapshot.py`
- Modify: `server/tests/test_data_export_archive.py`
- Modify: `server/tests/test_sync_event_coverage.py`
- Modify: `server/tests/test_account_deletion_worker.py`

- [x] **Step 1: 写生命周期失败测试**

创建三类订阅例外后断言：

```python
snapshot = read_snapshot(settings, owner_id)
assert {
    item["action"]
    for item in snapshot["calendar"]["subscription_exceptions"]
} == {"cancelled", "moved", "overridden"}
```

解包真实导出 ZIP 并验证：

- `data.json` 含完整本人例外；
- `csv/calendar.csv` 含 `kind=subscription_exception`；
- `calendar.ics` 的同 UID/RECURRENCE-ID 只有一个最终修订；
- 取消含 `STATUS:CANCELLED`，移动含本地时段，覆盖含最终字段；
- 不含 URL 查询参数、加密 URL、内部 lease、operation request/response。

同步断言创建、更新、删除各有一个 `calendar_source` 事件，重放和无变化更新不重复。注销
worker 完成后新表 owner 行为 0，控制账号不变。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_data_export_snapshot.py \
  tests/test_data_export_archive.py tests/test_sync_event_coverage.py \
  tests/test_account_deletion_worker.py
```

Expected: 快照键、CSV/ICS 修订及显式级联断言失败。

- [x] **Step 3: 增加 JSON 和 CSV 白名单**

`collect_export_snapshot()` 新增：

```python
"subscription_exceptions": await _rows(
    connection,
    CalendarSubscriptionException,
    (
        "id", "master_event_id", "source_occurrence_key",
        "original_start_at", "original_end_at", "original_all_day",
        "action", "moved_all_day", "moved_start_date", "moved_end_date",
        "moved_starts_at", "moved_ends_at", "moved_time_zone",
        "moved_ambiguous_offset_minutes", "moved_start_at", "moved_end_at",
        "override_name", "override_description", "override_location",
        "override_busy", "version", "created_at", "updated_at",
    ),
    owner_id,
    order=("master_event_id", "source_occurrence_key"),
),
```

`_calendar_rows()` 为每条例外追加 `kind=subscription_exception` 行，`parent_id` 使用
`master_event_id`，状态使用 action，标题/说明/地点只写本地覆盖值。

- [x] **Step 4: 合成最终 ICS 修订**

`_subscription_events()` 先以 `(master_event_id, source_occurrence_key)` 建例外映射。来源 detached
事件若被本地例外取代则跳过；本地例外生成同源 UID 的最终 `RECURRENCE-ID`：

```python
if exception["action"] == "cancelled":
    item.add("status", "CANCELLED")
elif exception["action"] == "moved":
    item.add("dtstart", moved_start)
    item.add("dtend", moved_end)
else:
    item.add("dtstart", original_start)
    item.add("dtend", original_end)
```

字段按“来源主事件 -> 来源 detached -> 字段覆盖 -> 单次覆盖”合并。解析异常只追加稳定 warning，
不把整个导出作业伪装成功但漏报。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_data_export_snapshot.py \
  tests/test_data_export_archive.py tests/test_data_export_api.py \
  tests/test_data_export_worker.py tests/test_sync_event_coverage.py \
  tests/test_sync_feed.py tests/test_account_deletion_worker.py
uv run ruff check src/itime/data_export tests/test_data_export_snapshot.py \
  tests/test_data_export_archive.py tests/test_sync_event_coverage.py \
  tests/test_account_deletion_worker.py
git add src/itime/data_export/snapshot.py src/itime/data_export/archive.py \
  tests/test_data_export_snapshot.py tests/test_data_export_archive.py \
  tests/test_sync_event_coverage.py tests/test_account_deletion_worker.py
git commit -m "feat: 导出并清理订阅单次例外"
```

## Task 4：实现统一日历交互

**Files:**
- Modify: `web/src/lib/calendar.ts`
- Modify: `web/src/components/CalendarOccurrencePanel.vue`
- Modify: `web/src/pages/CalendarPage.vue`
- Modify: `web/src/calendar.css`
- Modify: `web/e2e/calendar-subscriptions.spec.ts`

- [x] **Step 1: 编写失败的真实 Chromium 流程**

扩展受控 ICS scenario，所有业务操作通过可见 UI 完成：

```ts
await eventButton.click()
await page.getByRole('button', { name: '取消本次' }).click()
await expect(page.getByText('本次订阅安排已取消')).toBeVisible()
await expect(page.getByText('本地单次修改')).toBeVisible()
await page.getByRole('button', { name: '重置为来源' }).click()
await expect(page.getByText('已恢复当前来源安排')).toBeVisible()
```

继续覆盖：

- 定时移动后从原日消失并在新日期出现，冲突提示准确；
- 全天移动只显示开始/结束日期并保持天数；
- 内容覆盖只影响所选 occurrence，下一次仍为来源值；
- 刷新为新标题/RID 移动/来源取消后，本地例外仍按设计生效；
- 409 时显示“来源已变化”，重读后草稿不丢失并可明确重试；
- 管理来源按钮仍定位有效事件；
- 桌面、平板、手机都跑主流程，desktop 另跑 320×700 最大字号深色；
- axe、键盘、焦点恢复、console/pageerror、API 500 和根级横向溢出。

- [x] **Step 2: 运行测试确认失败**

```bash
cd web
pnpm exec playwright test e2e/calendar-subscriptions.spec.ts \
  --grep "订阅单次例外"
```

Expected: 三个项目因订阅详情只有“管理来源与本地覆盖”而失败。

- [x] **Step 3: 增加前端类型和 API**

`CalendarOccurrence` 增加：

```ts
source_version?: number | null
subscription_master_event_id?: string | null
source_status?: 'scheduled' | 'source_cancelled'
```

新增 `CalendarSubscriptionException` 及：

```ts
export async function saveSubscriptionOccurrenceException(
  occurrence: CalendarOccurrence,
  fields: SubscriptionExceptionFields,
  operationId: string,
) {
  if (!occurrence.source_id || !occurrence.subscription_master_event_id
      || !occurrence.source_occurrence_key
      || occurrence.source_version == null) {
    throw new Error('calendar_subscription_occurrence_not_editable')
  }
  const path = occurrence.exception_id
    ? `/calendar/subscriptions/${occurrence.source_id}/exceptions/${occurrence.exception_id}`
    : `/calendar/subscriptions/${occurrence.source_id}/exceptions`
  return api<SubscriptionExceptionWriteResult>(path, {
    operation_id: operationId,
    expected_source_version: occurrence.source_version,
    ...(occurrence.exception_id
      ? { expected_version: occurrence.exception_version }
      : {
          master_event_id: occurrence.subscription_master_event_id,
          source_occurrence_key: occurrence.source_occurrence_key,
        }),
    ...fields,
  }, occurrence.exception_id ? 'PUT' : 'POST')
}

export async function resetSubscriptionOccurrenceException(
  occurrence: CalendarOccurrence,
  operationId: string,
) {
  if (!occurrence.source_id || !occurrence.exception_id
      || occurrence.exception_version == null || occurrence.source_version == null) {
    throw new Error('calendar_subscription_exception_not_editable')
  }
  return api<SubscriptionExceptionWriteResult>(
    `/calendar/subscriptions/${occurrence.source_id}/exceptions/${occurrence.exception_id}`,
    {
      operation_id: operationId,
      expected_source_version: occurrence.source_version,
      expected_version: occurrence.exception_version,
    },
    'DELETE',
  )
}
```

创建使用 master ID/key/source version，更新使用 `exception_id/version`，重置使用 DELETE。任何必需
ID 缺失时抛出 `calendar_subscription_occurrence_not_editable`。

- [x] **Step 4: 改造详情交互**

`CalendarOccurrencePanel.vue` 让 `runCalendarAction()` 按来源类型调用对应 API。订阅 action 区固定
为：

```vue
<button v-if="occurrence.exception_id" type="button" class="button secondary"
  :disabled="pending" @click="resetSubscriptionOccurrence">
  <RotateCcw :size="17" aria-hidden="true" />重置为来源
</button>
<button v-else type="button" class="button secondary"
  :disabled="pending" @click="cancelOccurrence">
  <CircleSlash2 :size="17" aria-hidden="true" />取消本次
</button>
<button type="button" class="button secondary"
  :disabled="pending" @click="begin('move')">调整本次时间</button>
<button type="button" class="button secondary"
  :disabled="pending" @click="begin('override')">
  <Pencil :size="17" aria-hidden="true" />覆盖本次内容
</button>
```

全天移动表单使用排他结束日期；定时移动沿用 DST offset 选择。409 分支先保留 reactive 草稿，
`emit('changed', '来源已变化，已重新读取日历；请确认后重试。', 0)` 重读 occurrence 后允许用户
使用新 source version 再提交。

- [x] **Step 5: 同步刷新和样式**

`CalendarPage.vue` 的 `useSyncRefresh()` 继续接受 `calendar_source`。详情重读后按稳定完整
occurrence key 恢复选中项；若移动到当前范围外则关闭详情并显示成功状态。

`calendar.css` 为来源取消/本地保留提示、全天日期输入和操作换行增加稳定 grid/min-width；不新增
嵌套卡片和说明型页面文案。

- [x] **Step 6: 构建、专项验证并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/calendar-subscriptions.spec.ts \
  --grep "订阅单次例外"
git add src/lib/calendar.ts src/components/CalendarOccurrencePanel.vue \
  src/pages/CalendarPage.vue src/calendar.css \
  e2e/calendar-subscriptions.spec.ts
git commit -m "feat: 编辑订阅单次安排"
```

Expected: Vue 生产构建和 desktop/mobile/tablet 专项均通过。

## Task 5：受影响回归、全量验证与证据

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/设计/iTime-时间课表与重复规则设计.md`
- Modify: `doc/设计/iTime-ICS订阅设计.md`
- Modify: `doc/设计/iTime-订阅Occurrence单次例外设计.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/计划/iTime-M3订阅Occurrence单次例外实施计划.md`

- [x] **Step 1: 执行迁移和服务端受影响回归**

```bash
cd server
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run pytest -q tests/test_calendar_subscription_exception_models.py \
  tests/test_calendar_subscription_exceptions.py \
  tests/test_calendar_subscriptions.py tests/test_calendar_series.py \
  tests/test_calendar_exceptions.py tests/test_calendar_additions.py \
  tests/test_plan_source_conflicts.py tests/test_planning_inputs.py \
  tests/test_data_export_snapshot.py tests/test_data_export_archive.py \
  tests/test_data_export_api.py tests/test_data_export_worker.py \
  tests/test_sync_event_coverage.py tests/test_sync_feed.py \
  tests/test_account_deletion_api.py tests/test_account_deletion_worker.py
uv run ruff check .
uv build --no-sources
```

Expected: head 为 `0032_subscription_exceptions`；全部受影响测试、Ruff 和 sdist/wheel 构建
通过。

- [x] **Step 2: 执行前端受影响回归**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/calendar-subscriptions.spec.ts \
  e2e/calendar.spec.ts e2e/planning.spec.ts e2e/sync.spec.ts \
  e2e/data-exports.spec.ts e2e/account-deletion.spec.ts
```

Expected: 订阅、日历、规划、同步、导出和注销在适用三视口通过。

- [x] **Step 3: 执行完整回归**

```bash
cd server
uv run pytest -q

cd ../web
pnpm test:e2e
```

Expected: 服务端全量和 Chromium 全量通过；只允许测试文件中已说明的非桌面 skip。

- [x] **Step 4: 核对真实运行态与视觉证据**

```bash
curl --fail --silent http://127.0.0.1:8000/api/ready
curl --fail --silent http://localhost:5173/calendar
git diff --check
```

人工检查 `/tmp/itime-ui-evidence/*calendar-subscription-exception*` 的桌面、平板、手机和 320px
最大字号深色截图，确认无重叠、截断或横向滚动。检查前端 console 为 0 个未预期错误，并查询本地
PostgreSQL 确认 `itime_test_%` 测试库残留为 0。

- [x] **Step 5: 同步实际状态并提交**

只在真实命令完成后填写测试数量、耗时、截图和限制。更新追踪矩阵，把“订阅 occurrence 的用户
单次例外尚未实现”改为实际结果；Edge、真机、正式第三方来源兼容性和未完成的其他首版范围继续
保持未完成。

```bash
git add README.md doc/README.md \
  doc/需求/iTime-需求追踪矩阵.md \
  doc/设计/iTime-详细设计文档.md \
  doc/设计/iTime-时间课表与重复规则设计.md \
  doc/设计/iTime-ICS订阅设计.md \
  'doc/设计/iTime-订阅Occurrence单次例外设计.md' \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md \
  doc/验收/iTime-Web实施与验证.md \
  'doc/计划/iTime-M3订阅Occurrence单次例外实施计划.md'
git commit -m "docs: 记录订阅单次例外证据"
```

## 自检

- 规格覆盖：取消、移动、内容覆盖、重置、来源刷新、RID、来源取消、移入范围均有任务。
- 一致性：来源和例外写锁顺序一致；来源基线保持只读；本地例外不会被 refresh worker 重建。
- 性能：正常例外按最多 2000 个组合 key 精确读取，移入范围按 UTC 区间索引读取。
- 时间：全天排他结束、跨日、IANA 时区、DST 缺失和重复均有持久字段与测试。
- 生命周期：planning revision、同步、JSON/CSV/ICS、来源删除和账号注销均有显式覆盖。
- 测试：真实 PostgreSQL、HTTP、worker、并发、浏览器、多视口、axe 和视觉检查均进入验收门槛。
- 范围：不实现订阅系列编辑、补课、CalDAV 或自动计划重排。
