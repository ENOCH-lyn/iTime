# iTime M6 日历生命周期实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为手工/导入日历系列和 ICS 订阅源交付 30 天回收、恢复、到期脱敏、旧操作防复活及
可达 Web 回收站。

**Architecture:** 在 `CalendarSeries` 与 `CalendarSource` 聚合根保存生命周期字段，所有普通
投影和写入口在领域层统一排除非活动对象；订阅删除同时使租约失效，恢复时按原暂停状态决定
是否刷新并重新检测当前计划冲突。独立日历清理 worker 在 PostgreSQL 行锁保护下脱敏正文并
保留墓碑，既有计划、执行和复盘快照保持不可变。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、PostgreSQL 18、Alembic、Vue 3、
TypeScript、Lucide、IndexedDB、BroadcastChannel、Playwright Chromium、axe。

---

## 文件结构

服务端模型与迁移：

- `server/src/itime/migrations/versions/0034_calendar_lifecycle.py`：系列/来源生命周期字段、
 约束、索引、来源生命周期 planning revision 触发器和安全降级。
- `server/src/itime/calendar/models.py`：`CalendarSeries`、`CalendarSource` 生命周期 ORM。
- `server/src/itime/calendar/schemas.py`：系列删除/恢复输入。
- `server/tests/test_calendar_lifecycle_models.py`：真实 PostgreSQL 约束、索引、触发器及迁移往返。
- `server/tests/test_planning_models.py`：planning revision 触发表覆盖。

服务端领域与 API：

- `server/src/itime/calendar/lifecycle_service.py`：生命周期状态、系列/来源删除恢复、到期脱敏和
 操作响应失效。
- `server/src/itime/calendar/cleanup_worker.py`：常驻及 `--once` 清理入口。
- `server/src/itime/calendar/router.py`：系列生命周期筛选、删除和恢复路由。
- `server/src/itime/calendar/series_service.py`：系列序列化、可读/可写校验和普通投影过滤。
- `server/src/itime/calendar/subscription_router.py`：订阅来源生命周期筛选、删除和恢复路由。
- `server/src/itime/calendar/subscription_service.py`：来源生命周期序列化、刷新租约失效和 worker
 领取/提交保护。
- `server/src/itime/calendar/subscription_projection.py`：只投影活动来源。
- `server/src/itime/calendar/import_service.py`、`import_router.py`：回收对象的重复识别和旧候选
 阻断。
- `server/src/itime/planning/source_conflict_service.py`：删除时结束旧冲突、恢复时比较空基线与
 当前来源。
- `server/tests/test_calendar_lifecycle.py`：系列与订阅 API、投影、并发、租约和跨模块行为。
- `server/tests/test_calendar_lifecycle_cleanup.py`：脱敏、墓碑、批量、双 worker、回滚和独立进程。
- 现有日历、规划、同步、导出和账号注销测试：受影响行为回归。

导出与同步：

- `server/src/itime/data_export/snapshot.py`：生命周期字段、未清理订阅 URL 和墓碑白名单。
- `server/src/itime/data_export/archive.py`：活动 ICS、活动事件 CSV 和回收站 CSV/Markdown。
- `server/tests/test_data_export_snapshot.py`、`test_data_export_archive.py`：活动/回收/已清理内容。
- `server/tests/test_sync_event_coverage.py`：删除、恢复和清理无正文事件。
- `server/tests/test_account_deletion_worker.py`：账号注销与日历回收并发及级联回归。

Web：

- `web/src/lib/calendar.ts`：系列生命周期类型、筛选、删除与恢复客户端。
- `web/src/lib/calendar-subscriptions.ts`：订阅来源生命周期类型、筛选、删除与恢复客户端。
- `web/src/components/CalendarOccurrencePanel.vue`：活动系列“移到回收站”入口。
- `web/src/components/CalendarSubscriptionManager.vue`：暂停与删除分离、订阅回收状态。
- `web/src/components/CalendarTrashDialog.vue`：系列/订阅统一回收列表、只读详情和恢复。
- `web/src/components/CalendarNavigation.vue`：日历回收站入口。
- `web/src/pages/CalendarPage.vue`：对话框状态、同步失效和刷新。
- `web/src/calendar.css`：三视口、320px、大字号和深色布局。
- `web/e2e/calendar-lifecycle.spec.ts`：真实 API/worker/受控 ICS、多页面和多视口流程。

文档：

- `README.md`、`doc/README.md`、`designer.md`。
- `doc/需求/iTime-需求追踪矩阵.md`。
- `doc/设计/iTime-详细设计文档.md`、`iTime-时间课表与重复规则设计.md`、
  `iTime-ICS订阅设计.md`、`iTime-日历生命周期设计.md`。
- `doc/计划/iTime-开发总计划.md`、本计划。
- `doc/测试/iTime-测试方案与测试用例.md`。
- `doc/验收/iTime-产品验收清单.md`、`iTime-Web实施与验证.md`。
- `web/src/pages/PrivacyPage.vue`、`TermsPage.vue`。

## Task 1：建立日历生命周期模型与迁移

**Files:**
- Create: `server/src/itime/migrations/versions/0034_calendar_lifecycle.py`
- Modify: `server/src/itime/calendar/models.py`
- Create: `server/tests/test_calendar_lifecycle_models.py`
- Modify: `server/tests/test_planning_models.py`
- Modify: `server/tests/test_account_deletion_worker.py`

- [x] **Step 1: 写模型和迁移失败测试**

`test_calendar_lifecycle_models.py` 使用 `isolated_database` 升级到 head，分别插入一个系列和一个
订阅来源，再验证以下约束：

```python
connection.execute(
    """
    UPDATE calendar_series
    SET deleted_at=now(), purge_after=now() + interval '30 days'
    WHERE id=%s
    """,
    (series_id,),
)
assert connection.execute(
    """
    SELECT deleted_at IS NOT NULL, purge_after - deleted_at, purged_at
    FROM calendar_series WHERE id=%s
    """,
    (series_id,),
).fetchone() == (True, timedelta(days=30), None)

with pytest.raises(CheckViolation):
    with connection.transaction():
        connection.execute(
            "UPDATE calendar_sources SET deleted_at=now() WHERE id=%s",
            (source_id,),
        )
```

同文件还断言：

- 两表都存在 `deleted_at/purge_after/purged_at`；
- 活动状态三个字段全空，回收状态删除/到期时间同时存在，已清理状态仍保留删除/到期时间；
- `purge_after >= deleted_at` 且 `purged_at >= deleted_at`；
- 两表分别存在 owner/lifecycle 列表索引和 due 部分索引；
- 更新 `CalendarSeries.deleted_at` 和 `CalendarSource.deleted_at` 各使 planning revision 增加一次；
- 有任一生命周期数据时拒绝降级，清空生命周期字段后可降到 `0033_ai_fallback_routes` 再升级。

`test_planning_models.py` 的 `REVISION_TRIGGER_TABLES` 增加 `calendar_sources`，并确认来源触发器只在
生命周期字段更新时触发；名称修改不额外使规划提案过期。`test_account_deletion_worker.py`
继续验证 `calendar_series`、`calendar_sources` 由用户根行级联清空。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_calendar_lifecycle_models.py \
  tests/test_planning_models.py tests/test_account_deletion_worker.py
```

Expected: FAIL，`0034_calendar_lifecycle` 和 ORM 字段尚不存在。

- [x] **Step 3: 实现 ORM 字段与约束**

在两个聚合根加入相同字段：

```python
deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

两表 `__table_args__` 加入：

```python
CheckConstraint(
    "(deleted_at IS NULL AND purge_after IS NULL AND purged_at IS NULL) "
    "OR (deleted_at IS NOT NULL AND purge_after IS NOT NULL "
    "AND purge_after >= deleted_at "
    "AND (purged_at IS NULL OR purged_at >= deleted_at))",
    name="lifecycle_consistency",
)
```

索引命名为：

```text
ix_calendar_series_owner_lifecycle_updated
ix_calendar_series_purge_due
ix_calendar_sources_owner_lifecycle_updated
ix_calendar_sources_purge_due
```

- [x] **Step 4: 实现 `0034_calendar_lifecycle`**

迁移 down revision 为 `0033_ai_fallback_routes`。对两表添加字段、检查约束和索引，并创建只监听
来源生命周期列的触发器：

```sql
CREATE TRIGGER trg_planning_revision_calendar_sources_lifecycle
AFTER UPDATE OF deleted_at, purge_after, purged_at ON calendar_sources
FOR EACH ROW
WHEN (
  OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
  OR OLD.purge_after IS DISTINCT FROM NEW.purge_after
  OR OLD.purged_at IS DISTINCT FROM NEW.purged_at
)
EXECUTE FUNCTION bump_planning_input_revision()
```

降级先执行保护：

```sql
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM calendar_series
    WHERE deleted_at IS NOT NULL OR purge_after IS NOT NULL OR purged_at IS NOT NULL
  ) OR EXISTS (
    SELECT 1 FROM calendar_sources
    WHERE deleted_at IS NOT NULL OR purge_after IS NOT NULL OR purged_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'calendar lifecycle data must be cleared before downgrade';
  END IF;
END
$$;
```

然后按触发器、索引、约束、字段的逆序删除。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_calendar_lifecycle_models.py \
  tests/test_planning_models.py tests/test_account_deletion_worker.py
uv run ruff check src/itime/calendar/models.py \
  src/itime/migrations/versions/0034_calendar_lifecycle.py \
  tests/test_calendar_lifecycle_models.py tests/test_planning_models.py \
  tests/test_account_deletion_worker.py
uv run alembic upgrade head
uv run alembic current
uv run alembic check
git add src/itime/calendar/models.py \
  src/itime/migrations/versions/0034_calendar_lifecycle.py \
  tests/test_calendar_lifecycle_models.py tests/test_planning_models.py \
  tests/test_account_deletion_worker.py
git commit -m "feat: 建立日历对象生命周期模型"
```

Expected: 专项测试、迁移往返、head/check 和 Ruff 全部通过。

## Task 2：实现日历系列删除、恢复与普通投影隔离

**Files:**
- Create: `server/src/itime/calendar/lifecycle_service.py`
- Modify: `server/src/itime/calendar/schemas.py`
- Modify: `server/src/itime/calendar/series_service.py`
- Modify: `server/src/itime/calendar/router.py`
- Modify: `server/src/itime/calendar/import_service.py`
- Modify: `server/src/itime/calendar/import_router.py`
- Modify: `server/tests/test_calendar_lifecycle.py`
- Modify: `server/tests/test_calendar_series.py`
- Modify: `server/tests/test_calendar_exceptions.py`
- Modify: `server/tests/test_calendar_additions.py`
- Modify: `server/tests/test_calendar_imports.py`

- [x] **Step 1: 写系列生命周期 API 失败测试**

新增辅助函数：

```python
def lifecycle_payload(expected_version: int, operation_id=None) -> dict:
    return {
        "operation_id": str(operation_id or uuid4()),
        "expected_version": expected_version,
    }


def trash_series(client, series: dict, operation_id=None):
    return client.request(
        "DELETE",
        f"/api/v1/calendar/series/{series['id']}",
        json=lifecycle_payload(series["version"], operation_id),
    )


def restore_series(client, series: dict, operation_id=None):
    return client.post(
        f"/api/v1/calendar/series/{series['id']}/restore",
        json=lifecycle_payload(series["version"], operation_id),
    )
```

测试覆盖：

- 手工单次、重复、跨日、课程和拆分系列删除后从默认列表与 occurrence 查询消失；
- `lifecycle=trashed` 返回完整只读系列、删除时间、清理时间和剩余 30 天；
- 恢复原 ID 后，原 recurrence、例外、补课、学期和来源关系全部重新投影；
- 删除保留已确认计划版本和复盘快照，不修改历史内容；
- 删除与恢复分别使 planning revision 增加一次；
- 跨账号 404、缺 CSRF 403、陈旧版本 409、同 operation 重放、同 operation 异内容 409；
- 新 operation 重复删除不延长期限；活动对象恢复 409；已清理对象读写 410；
- 回收对象拒绝更新、拆分、新增/编辑单次例外和补课；
- 文件和截图导入产生的系列可以独立回收，订阅 occurrence 不能走系列接口。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_calendar_lifecycle.py \
  tests/test_calendar_series.py tests/test_calendar_exceptions.py \
  tests/test_calendar_additions.py tests/test_calendar_imports.py
```

Expected: FAIL，生命周期 schema、路由和过滤尚不存在。

- [x] **Step 3: 定义系列生命周期合同**

`calendar/schemas.py` 增加：

```python
class SeriesLifecycleInput(OperationInput):
    expected_version: int = Field(ge=1)
```

`calendar/lifecycle_service.py` 增加：

```python
CALENDAR_TRASH_RETENTION = timedelta(days=30)


def calendar_lifecycle(item: CalendarSeries | CalendarSource) -> str:
    if item.purged_at is not None:
        return "purged"
    if item.deleted_at is not None:
        return "trashed"
    return "active"


def ensure_calendar_readable(item: CalendarSeries | CalendarSource) -> None:
    if item.purged_at is not None:
        raise AppError(
            "calendar_item_purged",
            "此日历内容已超过回收站恢复期并完成清理。",
            status=410,
        )


def ensure_calendar_editable(item: CalendarSeries | CalendarSource) -> None:
    ensure_calendar_readable(item)
    if item.deleted_at is not None:
        raise AppError(
            "calendar_item_trashed",
            "此日历内容在回收站中，请先恢复后再修改。",
            status=409,
            details={"lifecycle": "trashed", "current_version": item.version},
        )
```

`series_result` 增加：

```python
"lifecycle": calendar_lifecycle(series),
"deleted_at": series.deleted_at,
"purge_after": series.purge_after,
"purged_at": series.purged_at,
"trash_remaining_days": remaining_days(series.purge_after),
```

`locked_series(session, owner_id, series_id, *, editable: bool = True)` 默认调用
`ensure_calendar_editable`；删除、恢复和回收详情传 `editable=False`。

- [x] **Step 4: 实现系列列表和写路由**

`GET /calendar/series` 新增：

```python
lifecycle: Literal["active", "trashed"] = "active"
```

过滤固定为：

```python
if lifecycle == "active":
    statement = statement.where(
        CalendarSeries.deleted_at.is_(None),
        CalendarSeries.purged_at.is_(None),
    )
else:
    statement = statement.where(
        CalendarSeries.deleted_at.is_not(None),
        CalendarSeries.purged_at.is_(None),
    )
```

新增 `trash_calendar_series` 和 `restore_calendar_series` 领域函数。删除写入一次固定期限并发送
`calendar_series/deleted`；恢复要求尚未到期，清空删除字段并发送
`calendar_series/restored`。两者都复用 `replay_operation/save_operation`，版本变化和响应在同一
事务提交。

路由固定为：

```python
@router.delete("/calendar/series/{series_id}")
async def trash_calendar_series_route(
    series_id: UUID,
    data: SeriesLifecycleInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await trash_calendar_series(
            identity.session,
            identity.user.id,
            series_id,
            data,
            current_time=now(),
        )


@router.post("/calendar/series/{series_id}/restore")
async def restore_calendar_series_route(
    series_id: UUID,
    data: SeriesLifecycleInput,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await restore_calendar_series(
            identity.session,
            identity.user.id,
            series_id,
            data,
            current_time=now(),
        )
```

- [x] **Step 5: 过滤所有普通系列读取**

`query_occurrences`、`find_series_conflicts`、移动例外补查和补课补查都增加活动生命周期过滤。
`import_router.py` 在读取 `confirmed_series_id/duplicate_series_id` 时：

- 未清理回收对象返回 `calendar_series_trashed` 和可恢复 ID；
- 已清理墓碑不作为可编辑系列返回；
- 旧候选确认不能把现有回收对象重新标为活动。

所有现有系列写测试增加回收对象 409 断言，确保不是只在主更新接口过滤。

- [x] **Step 6: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_calendar_lifecycle.py \
  tests/test_calendar_series.py tests/test_calendar_exceptions.py \
  tests/test_calendar_additions.py tests/test_calendar_imports.py
uv run ruff check src/itime/calendar/lifecycle_service.py \
  src/itime/calendar/schemas.py src/itime/calendar/series_service.py \
  src/itime/calendar/router.py src/itime/calendar/import_service.py \
  src/itime/calendar/import_router.py \
  tests/test_calendar_lifecycle.py tests/test_calendar_series.py \
  tests/test_calendar_exceptions.py tests/test_calendar_additions.py \
  tests/test_calendar_imports.py
git add src/itime/calendar/lifecycle_service.py \
  src/itime/calendar/schemas.py src/itime/calendar/series_service.py \
  src/itime/calendar/router.py src/itime/calendar/import_service.py \
  src/itime/calendar/import_router.py \
  tests/test_calendar_lifecycle.py tests/test_calendar_series.py \
  tests/test_calendar_exceptions.py tests/test_calendar_additions.py \
  tests/test_calendar_imports.py
git commit -m "feat: 回收并恢复日历系列"
```

Expected: 系列生命周期专项和全部受影响日历服务测试通过。

## Task 3：实现订阅源删除、恢复与刷新租约失效

**Files:**
- Modify: `server/src/itime/calendar/lifecycle_service.py`
- Modify: `server/src/itime/calendar/subscription_router.py`
- Modify: `server/src/itime/calendar/subscription_service.py`
- Modify: `server/src/itime/calendar/subscription_projection.py`
- Modify: `server/src/itime/planning/source_conflict_service.py`
- Modify: `server/tests/test_calendar_lifecycle.py`
- Modify: `server/tests/test_plan_source_conflicts.py`
- Test: `server/tests/test_calendar_subscriptions.py`
- Test: `server/tests/test_calendar_subscription_exceptions.py`

- [x] **Step 1: 写订阅生命周期和租约竞态失败测试**

测试创建真实订阅来源并领取刷新：

```python
claim = await claim_next_subscription(database, lease_seconds=30)
assert claim is not None

deleted = client.request(
    "DELETE",
    f"/api/v1/calendar/subscriptions/{source_id}",
    json={
        "operation_id": str(uuid4()),
        "expected_version": source_version,
    },
)
assert deleted.status_code == 200
assert deleted.json()["subscription"]["lifecycle"] == "trashed"

assert not await complete_subscription_refresh(
    database,
    claim,
    fetched_snapshot,
)
```

同文件覆盖：

- 活动和暂停订阅删除后均从默认列表及普通日历消失，回收列表保留完整内容；
- 删除运行中刷新将对应 refresh 标为 `stale`、清租约、推进 `fetch_revision`；
- worker 领取和成功/失败提交都再次校验来源活动状态；
- 回收订阅拒绝更新 URL、手动刷新、暂停/恢复、字段覆盖和单次例外；
- 原为活动的订阅恢复后 queued，原为暂停的订阅恢复后仍 paused/idle；
- 恢复时以空基线对比已保存来源事件，当前计划产生来源变化提示但不被改写；
- 删除将旧开放来源冲突标为 `resolved/superseded`；
- 相同 URL 存在回收来源时新建返回 409 和 `restore_source_id`；
- 跨账号、版本、幂等、到期和删除/恢复并发；
- 删除后查询 source-specific occurrence 返回空集合，而非绕过普通投影过滤。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_calendar_lifecycle.py \
  tests/test_calendar_subscription_exceptions.py \
  tests/test_calendar_subscriptions.py \
  tests/test_plan_source_conflicts.py
```

Expected: FAIL，来源生命周期接口和 worker 防线尚不存在。

- [x] **Step 3: 提取刷新失效函数并统一可编辑检查**

把 `subscription_router._stale_running_refresh` 移到
`subscription_service.stale_running_refresh`，保持现有错误码和 refresh 终态。扩展：

`locked_subscription(session, owner_id, source_id, *, for_update=False, editable=True)` 在完成现有
来源和订阅查询后先调用 `ensure_calendar_readable(source)`；`editable=True` 时继续调用
`ensure_calendar_editable(source)`，最后返回 `(source, subscription)`。

普通更新、刷新、暂停/恢复、覆盖和例外继续使用 `editable=True`；详情、删除和恢复显式传
`editable=False`。

`subscription_result` 增加来源生命周期字段和剩余天数，保留订阅
`lifecycle_status=active|paused` 作为独立刷新状态。

- [x] **Step 4: 实现删除与恢复事务**

在 `lifecycle_service.py` 实现
`trash_subscription_source(session: AsyncSession, owner_id: UUID, source_id: UUID,
data: SubscriptionAction, *, current_time: datetime) -> dict` 和
`restore_subscription_source(session: AsyncSession, owner_id: UUID, source_id: UUID,
data: SubscriptionAction, *, current_time: datetime) -> dict`。

删除锁顺序固定为来源、订阅、当前 refresh、开放 `PlanSourceConflict`；失效租约，设置
`deleted_at/purge_after`，将 refresh 状态置 idle，增加来源和订阅版本，并发出
`calendar_source/deleted`。

恢复先调用 `capture_source_plan_baselines` 获得“来源仍隐藏”的空基线，再清除生命周期字段。
原订阅为 active 时排队并更新时间，paused 保持 idle；增加 `generation` 后调用
`record_source_plan_conflicts` 比较已保存来源事件，最后发出 `calendar_source/restored`。

新增路由：

```python
@router.delete("/{source_id}")
async def trash_subscription(
    source_id: UUID,
    data: SubscriptionAction,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await trash_subscription_source(
            identity.session,
            identity.user.id,
            source_id,
            data,
            current_time=now(),
        )


@router.post("/{source_id}/restore")
async def restore_subscription(
    source_id: UUID,
    data: SubscriptionAction,
    request: Request,
) -> dict:
    async with authorized(request, write=True) as identity:
        return await restore_subscription_source(
            identity.session,
            identity.user.id,
            source_id,
            data,
            current_time=now(),
        )
```

列表新增 `lifecycle=active|trashed`，默认活动。

- [x] **Step 5: 封住 worker、投影和冲突查询旁路**

`claim_next_subscription` 连接 `CalendarSource` 并要求三个生命周期字段为空；
`_locked_claim_subscription` 同样连接并锁定活动来源。`query_subscription_occurrences` 连接
活动来源，即使指定 `source_id` 也不能读取回收内容。

`open_source_conflict_summaries` 只返回活动来源的开放冲突。增加
`supersede_open_source_conflicts(session, owner_id, source_id, at)`，删除事务用它结束旧提示。

创建订阅在比较 `url_hmac` 时同时检查活动和未清理回收来源；回收命中返回：

```python
raise AppError(
    "calendar_subscription_trashed_duplicate",
    "相同地址的订阅在回收站中，请恢复后继续使用。",
    status=409,
    details={"restore_source_id": str(existing.source_id)},
)
```

- [x] **Step 6: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_calendar_lifecycle.py \
  tests/test_calendar_subscription_exceptions.py \
  tests/test_calendar_subscriptions.py \
  tests/test_plan_source_conflicts.py
uv run ruff check src/itime/calendar/lifecycle_service.py \
  src/itime/calendar/subscription_router.py \
  src/itime/calendar/subscription_service.py \
  src/itime/calendar/subscription_projection.py \
  src/itime/planning/source_conflict_service.py \
  tests/test_calendar_lifecycle.py tests/test_calendar_subscriptions.py \
  tests/test_calendar_subscription_exceptions.py \
  tests/test_plan_source_conflicts.py
git add src/itime/calendar/lifecycle_service.py \
  src/itime/calendar/subscription_router.py \
  src/itime/calendar/subscription_service.py \
  src/itime/calendar/subscription_projection.py \
  src/itime/planning/source_conflict_service.py \
  tests/test_calendar_lifecycle.py tests/test_calendar_subscriptions.py \
  tests/test_calendar_subscription_exceptions.py \
  tests/test_plan_source_conflicts.py
git commit -m "feat: 回收并恢复日历订阅"
```

Expected: 订阅生命周期、真实 worker、计划冲突和既有订阅测试全部通过。

## Task 4：实现到期脱敏与并发清理进程

**Files:**
- Modify: `server/src/itime/calendar/lifecycle_service.py`
- Create: `server/src/itime/calendar/cleanup_worker.py`
- Modify: `server/src/itime/calendar/import_service.py`
- Create: `server/tests/test_calendar_lifecycle_cleanup.py`
- Modify: `server/tests/test_calendar_imports.py`
- Modify: `server/tests/test_account_deletion_worker.py`

- [x] **Step 1: 写清理失败测试**

构造到期与未到期系列、到期订阅、导入候选、例外、补课、订阅事件、覆盖、刷新、操作响应和
计划来源冲突，调用：

```python
result = await cleanup_expired_calendar_items(database, limit=100)
assert result == {"series": 1, "sources": 1}
```

断言：

- 未到期对象和 pending-deletion 账号完全不变；
- 到期系列正文、地点、日程参数、学期/来源关联被清除，例外和补课为 0；
- 关联导入候选不再引用系列且正文清空，导入身份删除；
- 入站 `predecessor_id` 仍可引用墓碑，不破坏系列拆分外键；
- 到期来源不含任何原 URL 密文、HMAC、展示主机、错误、etag 或最终地址；
- 订阅事件、覆盖、例外和刷新为 0，计划来源冲突及计划快照保持；
- 匹配对象 ID 的 `UserOperation.response` 清空并写 `response_purged_at`；
- 每个墓碑版本增加并产生无正文 `purged` 同步事件；
- `limit` 只允许 1—100；两个 worker 不重复处理；
- 清理中抛错时本聚合全部回滚，再次执行可成功；
- `python -m itime.calendar.cleanup_worker --once` 在独立进程真实退出 0；
- 账号注销先锁用户时清理跳过，注销级联后不残留孤儿。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_calendar_lifecycle_cleanup.py \
  tests/test_calendar_imports.py tests/test_account_deletion_worker.py
```

Expected: FAIL，清理函数和 worker 尚不存在。

- [x] **Step 3: 实现系列到期清理**

`expire_calendar_operation_responses` 复用现有 `UserOperation` 模式，按 scope 或 JSON 响应中的 UUID
清空旧响应。

`_purge_series` 在同一事务中：

```python
await session.execute(
    delete(CalendarException).where(CalendarException.series_id == series.id)
)
await session.execute(
    delete(CalendarAddition).where(CalendarAddition.series_id == series.id)
)
await session.execute(
    delete(CalendarImportIdentity).where(CalendarImportIdentity.series_id == series.id)
)
await session.execute(
    update(CalendarImportCandidate)
    .where(
        or_(
            CalendarImportCandidate.confirmed_series_id == series.id,
            CalendarImportCandidate.duplicate_series_id == series.id,
        )
    )
    .values(
        state="skipped",
        raw_data={},
        draft=None,
        external_uid=None,
        external_recurrence_id=None,
        warnings=[],
        error_code=None,
        error_message=None,
        error_details=None,
        confirmed_series_id=None,
        duplicate_series_id=None,
        version=CalendarImportCandidate.version + 1,
        updated_at=current_time,
    )
)
```

然后把系列改为合法固定墓碑：

```python
series.term_id = None
series.predecessor_id = None
series.source_id = None
series.kind = "fixed"
series.name = "已彻底删除的安排"
series.description = ""
series.location = ""
series.busy = False
series.source_type = "manual"
series.time_zone = "UTC"
series.start_date = date(1970, 1, 1)
series.starts_at = time(0, 0)
series.ends_at = time(0, 0)
series.end_day_offset = 1
series.start_period_number = None
series.end_period_number = None
series.recurrence_frequency = "none"
series.recurrence_interval = 1
series.recurrence_weekdays = []
series.recurrence_month = None
series.recurrence_day_of_month = None
series.recurrence_until = None
series.recurrence_count = None
series.course_first_week_monday = None
series.course_week_numbers = []
series.ambiguous_offset_minutes = None
series.purged_at = current_time
```

最后增加版本、更新时间并发送 `calendar_series/purged`。

- [x] **Step 4: 实现订阅来源到期清理**

先删除 `CalendarSubscriptionEvent` 和 `CalendarSubscriptionRefresh`；事件级覆盖和例外由外键
级联。保留 `PlanSourceConflict`。订阅改为稳定墓碑：

```python
source.name = "已彻底删除的订阅"
subscription.encrypted_url = b""
subscription.url_hmac = "0" * 64
subscription.display_url = "已清理"
subscription.display_host = "deleted.invalid"
subscription.default_time_zone = "UTC"
subscription.lifecycle_status = "paused"
subscription.refresh_status = "idle"
subscription.next_refresh_at = current_time
subscription.fetch_revision += 1
subscription.lease_id = None
subscription.lease_until = None
subscription.etag = None
subscription.last_modified = None
subscription.encrypted_final_url = None
subscription.final_url_hmac = None
subscription.final_host = None
subscription.content_sha256 = None
subscription.last_error_code = None
subscription.last_error_message = None
source.purged_at = current_time
```

增加来源/订阅版本并发送 `calendar_source/purged`。

- [x] **Step 5: 实现有界 worker 和账号锁顺序**

`cleanup_expired_calendar_items` 的 SQL 同时连接 `User`，只领取 `status=active` 的对象，并以
`User -> CalendarSeries/CalendarSource` 顺序 `FOR UPDATE SKIP LOCKED`，与账号注销的用户优先
锁顺序一致。每类最多处理 `limit` 条，返回实际计数。

`cleanup_worker.py` 固定入口：

```python
async def run(*, once: bool = False) -> None:
    configure_logging()
    database = Database(get_settings())
    try:
        while True:
            try:
                result = await cleanup_expired_calendar_items(database)
            except Exception as exc:
                logger.warning(
                    "calendar_cleanup_worker_error",
                    extra={"error_type": type(exc).__name__},
                )
                if once:
                    raise RuntimeError(
                        "日历清理进程失败，请检查数据库状态。"
                    ) from None
                result = {"series": 0, "sources": 0}
            if once:
                break
            if not any(result.values()):
                await asyncio.sleep(60)
    finally:
        await database.close()
```

- [x] **Step 6: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_calendar_lifecycle_cleanup.py \
  tests/test_calendar_imports.py tests/test_account_deletion_worker.py
uv run ruff check src/itime/calendar/lifecycle_service.py \
  src/itime/calendar/cleanup_worker.py \
  src/itime/calendar/import_service.py \
  tests/test_calendar_lifecycle_cleanup.py tests/test_calendar_imports.py \
  tests/test_account_deletion_worker.py
uv run python -m itime.calendar.cleanup_worker --once
git add src/itime/calendar/lifecycle_service.py \
  src/itime/calendar/cleanup_worker.py \
  src/itime/calendar/import_service.py \
  tests/test_calendar_lifecycle_cleanup.py tests/test_calendar_imports.py \
  tests/test_account_deletion_worker.py
git commit -m "feat: 清理到期日历回收对象"
```

Expected: 脱敏、双 worker、回滚、账号竞争和独立进程测试全部通过。

## Task 5：接入规划、同步和本人导出

**Files:**
- Modify: `server/src/itime/data_export/snapshot.py`
- Modify: `server/src/itime/data_export/archive.py`
- Modify: `server/tests/test_planning_inputs.py`
- Modify: `server/tests/test_data_export_snapshot.py`
- Modify: `server/tests/test_data_export_archive.py`
- Modify: `server/tests/test_sync_event_coverage.py`
- Test: `server/tests/test_review_sessions.py`
- Test: `server/tests/test_sync_feed.py`

- [x] **Step 1: 写跨模块失败测试**

专项断言：

```python
snapshot = await collect_data_snapshot(connection, owner_id, settings)
series = next(item for item in snapshot["calendar"]["series"]
              if item["id"] == str(trashed_series_id))
assert series["lifecycle"] == "trashed"
assert series["name"] == "可恢复安排"

purged_source = next(item for item in snapshot["calendar"]["sources"]
                     if item["id"] == str(purged_source_id))
assert purged_source["lifecycle"] == "purged"
assert purged_source["name"] == "已彻底删除的订阅"
assert next(item for item in snapshot["calendar"]["subscriptions"]
            if item["source_id"] == str(purged_source_id))["url"] is None
```

同时覆盖：

- 规划输入和复盘事实不含回收/已清理系列或来源；
- 删除前已保存的 `PlanningInputSnapshot.fixed_occurrences` 和报告版本不被改写；
- JSON 包含活动、回收和墓碑元数据，但墓碑无原正文和 URL；
- `calendar/itime.ics` 与 `csv/calendar-events.csv` 只含活动对象；
- 新增 `csv/calendar-trash.csv` 和 `markdown/calendar-trash.md`，只列未清理回收聚合及到期时间；
- 回收订阅 JSON 仍含本人 URL，已清理订阅 URL 为 null；
- 删除、恢复、清理事件只有类型、ID、动作和版本，无名称、URL、地点或正文；
- 游标从另一设备读取事件后触发日历页刷新，过期游标重建仍不显示回收内容。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_planning_inputs.py tests/test_review_sessions.py \
  tests/test_data_export_snapshot.py tests/test_data_export_archive.py \
  tests/test_sync_event_coverage.py tests/test_sync_feed.py
```

Expected: FAIL，导出格式和生命周期跨模块断言尚未满足。

- [x] **Step 3: 验证规划与复盘读取**

`snapshot_service.py` 通过 `query_occurrences` 读取固定安排，因而复用 Task 2/3 的统一生命周期
过滤；`fact_service.py` 只读取已保存计划、任务、专注和困难事实，不直接查询日历聚合。专项测试
用直接数据库构造的回收对象确认规划上下文不含回收内容，并保持既有复盘事实回归通过。活动条件
固定为：

```python
CalendarSeries.deleted_at.is_(None),
CalendarSeries.purged_at.is_(None),
CalendarSource.deleted_at.is_(None),
CalendarSource.purged_at.is_(None),
```

测试使用直接数据库构造的回收对象，避免仅证明 API 删除路径。

- [x] **Step 4: 扩展完整 JSON 快照**

`snapshot.py` 的来源和系列白名单加入：

```text
deleted_at
purge_after
purged_at
```

在导出订阅时连接来源生命周期。只有 `purged_at is None` 且密文非空才调用
`decrypt_subscription_url`；已清理对象写：

```python
item["url"] = None
item["lifecycle"] = "purged"
```

活动和回收对象分别写 `active/trashed`。不得把密钥、HMAC 或墓碑占位密文加入 JSON。

- [x] **Step 5: 扩展分类归档**

`EXPECTED_EXPORT_PATHS` 增加：

```python
"csv/calendar-trash.csv",
"markdown/calendar-trash.md",
```

`_series_events` 和订阅 ICS 组合只处理 lifecycle active。`calendar-trash.csv` 固定字段：

```text
kind,id,title,source_type,source_host,deleted_at,purge_after,lifecycle
```

Markdown 以“日历回收站”为一级标题，按删除时间列出类型、名称、删除时间和清理时间，不写完整
订阅 URL。manifest 计数和 SHA-256 必须包含新增文件。

- [x] **Step 6: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_planning_inputs.py tests/test_review_sessions.py \
  tests/test_data_export_snapshot.py tests/test_data_export_archive.py \
  tests/test_sync_event_coverage.py tests/test_sync_feed.py
uv run ruff check src/itime/data_export/snapshot.py \
  src/itime/data_export/archive.py tests/test_planning_inputs.py \
  tests/test_data_export_snapshot.py \
  tests/test_data_export_archive.py tests/test_sync_event_coverage.py \
  tests/test_sync_feed.py
git add src/itime/data_export/snapshot.py \
  src/itime/data_export/archive.py tests/test_planning_inputs.py \
  tests/test_data_export_snapshot.py \
  tests/test_data_export_archive.py tests/test_sync_event_coverage.py \
  doc/计划/iTime-M6日历生命周期实施计划.md
git commit -m "feat: 同步并导出日历回收状态"
```

Expected: 规划、复盘、同步与导出专项全部通过，ZIP 可真实解包并核对新增文件摘要。

## Task 6：接入 Web 客户端与直接删除入口

**Files:**
- Modify: `web/src/lib/calendar.ts`
- Modify: `web/src/lib/calendar-subscriptions.ts`
- Modify: `web/src/components/CalendarOccurrencePanel.vue`
- Modify: `web/src/components/CalendarSubscriptionManager.vue`
- Modify: `web/src/calendar.css`
- Modify: `web/e2e/calendar-lifecycle.spec.ts`

- [x] **Step 1: 写直接删除 UI 失败测试**

在新 spec 中真实注册并创建重复安排：

```ts
await goCalendar(page, targetDate)
await eventButton(page, targetDate, '回收测试安排').click()
page.once('dialog', dialog => dialog.accept())
await page.getByRole('button', { name: '移到回收站', exact: true }).click()
await expect(page.getByText('安排已移到回收站，将保留 30 天。')).toBeVisible()
await expect(eventButton(page, targetDate, '回收测试安排')).toHaveCount(0)
```

订阅流程断言暂停说明与删除说明不同，并确认删除请求完成后原来源事件立即从日历消失。

- [x] **Step 2: 运行测试确认失败**

```bash
cd web
pnpm exec playwright test e2e/calendar-lifecycle.spec.ts --project=desktop \
  --grep "直接删除"
```

Expected: FAIL，按钮和客户端 API 尚不存在。

- [x] **Step 3: 扩展 TypeScript 合同**

两类对象增加：

```ts
export type CalendarLifecycle = 'active' | 'trashed' | 'purged'

export interface CalendarLifecycleFields {
  lifecycle: CalendarLifecycle
  deleted_at: string | null
  purge_after: string | null
  purged_at: string | null
  trash_remaining_days: number | null
}
```

`CalendarSeries` 和 `CalendarSubscription` 扩展该接口。`listSeries`、
`listCalendarSubscriptions` 接受 `lifecycle: 'active' | 'trashed' = 'active'`。

新增函数：

```ts
export async function trashCalendarSeries(
  series: CalendarSeries,
  operationId = crypto.randomUUID(),
) {
  return (await api<{ series: CalendarSeries }>(
    `/calendar/series/${series.id}`,
    { operation_id: operationId, expected_version: series.version },
    'DELETE',
  )).series
}

export async function restoreCalendarSeries(
  series: CalendarSeries,
  operationId = crypto.randomUUID(),
) {
  return (await api<{ series: CalendarSeries }>(
    `/calendar/series/${series.id}/restore`,
    { operation_id: operationId, expected_version: series.version },
  )).series
}

export async function trashCalendarSubscription(
  subscription: CalendarSubscription,
  operationId = crypto.randomUUID(),
) {
  return (await api<SubscriptionResponse>(
    `/calendar/subscriptions/${subscription.id}`,
    { operation_id: operationId, expected_version: subscription.version },
    'DELETE',
  )).subscription
}

export async function restoreCalendarSubscription(
  subscription: CalendarSubscription,
  operationId = crypto.randomUUID(),
) {
  return (await api<SubscriptionResponse>(
    `/calendar/subscriptions/${subscription.id}/restore`,
    { operation_id: operationId, expected_version: subscription.version },
  )).subscription
}
```

DELETE 请求继续通过 `api` 发送 JSON body 和 CSRF，不改成 query 参数。

- [x] **Step 4: 增加系列删除交互**

`CalendarOccurrencePanel` 仅对非订阅 calendar occurrence 显示带 `Trash2` 的“移到回收站”。
确认文案固定为：

```text
删除后安排会立即从普通日历隐藏，并在回收站保留 30 天。已有计划和复盘历史不会改写。
```

使用 `{id,fingerprint}` 保留未知响应下的 operation ID；成功后先发 `close`，再发：

```ts
emit('changed', '安排已移到回收站，将保留 30 天。已有计划未被改写。', 0)
```

错误继续交给 `StatusMessage`，版本冲突显示请求编号并允许重新读取，不关闭详情。

- [x] **Step 5: 区分订阅暂停与删除**

订阅详情动作区增加 `Trash2` 删除按钮。暂停说明保持“停止刷新、现有内容保留”，删除使用组件
内原生 `<dialog>`，确认区显示 `syncStatus.pending_count` 和当前
`detail.subscription.refresh_status`，明确“立即从日历隐藏、30 天内恢复”。删除中锁定对话框和
动作，成功后关闭当前详情、刷新活动列表并把焦点移到状态消息。

- [x] **Step 6: 构建、专项验证并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/calendar-lifecycle.spec.ts --project=desktop \
  --grep "直接删除"
pnpm exec playwright test e2e/calendar.spec.ts e2e/calendar-subscriptions.spec.ts \
  --project=desktop
git add src/lib/calendar.ts src/lib/calendar-subscriptions.ts \
  src/components/CalendarOccurrencePanel.vue \
  src/components/CalendarSubscriptionManager.vue \
  src/calendar.css \
  e2e/calendar-lifecycle.spec.ts
git commit -m "feat: 从日历回收安排与订阅"
```

Expected: 生产构建、直接删除流程和既有桌面日历流程通过。

## Task 7：实现统一日历回收站与恢复界面

**Files:**
- Create: `web/src/components/CalendarTrashDialog.vue`
- Modify: `web/src/components/CalendarNavigation.vue`
- Modify: `web/src/pages/CalendarPage.vue`
- Modify: `web/src/calendar.css`
- Modify: `web/e2e/calendar-lifecycle.spec.ts`
- Modify: `web/e2e/sync.spec.ts`

- [x] **Step 1: 写回收、恢复和跨设备失败测试**

浏览器流程：

```ts
await page.getByRole('button', { name: '日历回收站', exact: true }).click()
const trash = page.getByRole('dialog', { name: '日历回收站' })
await expect(trash.getByText('回收测试安排')).toBeVisible()
await expect(trash.getByText(/将在 .* 清理/)).toBeVisible()
await trash.getByRole('button', { name: '恢复回收测试安排' }).click()
await expect(page.getByText('安排已恢复到日历。')).toBeVisible()
```

完整覆盖：

- 系列和订阅分段、空态、加载、首次失败重试和已有列表刷新失败保旧；
- 回收详情只读，无编辑、刷新、暂停或永久删除；
- 恢复系列重新显示原例外/补课，恢复活动订阅进入 queued 并由真实 worker 刷新；
- 恢复暂停订阅仍不请求受控上游；
- 恢复订阅与当前计划重叠后出现来源复核入口，计划块不移动；
- 两页面制造删除/编辑和恢复/再次删除冲突，保留请求编号；
- 第二浏览器上下文在 5 秒内看到删除和恢复；
- 离线旧操作恢复网络后显示 lifecycle 冲突，不覆盖服务端；
- desktop、mobile、tablet、320×700 最大字号深色、键盘、粗指针、减少动效、axe、控制台、
  API 500、Vite 错误层和根级横向溢出。

- [x] **Step 2: 运行测试确认失败**

```bash
cd web
pnpm exec playwright test e2e/calendar-lifecycle.spec.ts --project=desktop
pnpm exec playwright test e2e/sync.spec.ts --project=desktop \
  --grep "calendar lifecycle"
```

Expected: FAIL，导航入口和回收对话框尚不存在。

- [x] **Step 3: 实现 `CalendarTrashDialog`**

组件公开合同：

```ts
defineProps<{ open: boolean }>()
const emit = defineEmits<{
  close: []
  changed: [message: string]
}>()
```

打开时并行读取 `listSeries('trashed')` 和
`listCalendarSubscriptions('trashed')`。内部使用“安排 / 订阅”分段控件，每项显示类型、名称、
原范围或脱敏主机、删除时间、清理时间和剩余天数。展开项只读；恢复按钮按对象类型调用对应
API，未知结果继续复用原 operation ID。

首次加载失败显示原因、保存状态、请求编号和重试；已有列表刷新失败保留旧内容。恢复成功移除
该项、显示状态并 `emit('changed', message)`；对话框关闭后焦点回到导航按钮。

- [x] **Step 4: 接入导航和页面刷新**

`CalendarNavigation` 导入 `Trash2`，新增：

```ts
openTrash: []
```

并在“导入日历”与“时间与学期”之间增加：

```vue
<button
  type="button"
  class="nav-link"
  data-calendar-trash-trigger
  @click="emit('openTrash')"
>
  <Trash2 :size="18" aria-hidden="true" />日历回收站
</button>
```

`CalendarPage` 保存 `trashOpen`，在同步事件
`calendar_series/calendar_source` 的 `deleted/restored/purged` 动作到达时刷新活动日历和打开的
回收列表。任一管理对话框打开时不重复弹层；关闭后恢复触发按钮焦点。

- [x] **Step 5: 完成响应式样式**

桌面使用最大 880px 对话框和两列摘要/详情；840px 以下单列，600px 以下全屏内部滚动。
列表行和操作区使用稳定网格，不嵌套卡片。固定：

```css
.calendar-trash-dialog {
  width: min(880px, calc(100vw - 32px));
  max-height: min(820px, calc(100vh - 32px));
}

.calendar-trash-list {
  min-width: 0;
  overflow-wrap: anywhere;
}
```

320px 最大字号下分段控件和按钮允许换行，但图标按钮保持 44px 触控尺寸。

- [x] **Step 6: 三视口验证并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/calendar-lifecycle.spec.ts
pnpm exec playwright test e2e/sync.spec.ts --project=desktop \
  --grep "calendar lifecycle"
git add src/components/CalendarTrashDialog.vue \
  src/components/CalendarNavigation.vue src/pages/CalendarPage.vue \
  src/calendar.css e2e/calendar-lifecycle.spec.ts e2e/sync.spec.ts
git commit -m "feat: 管理并恢复日历回收内容"
```

Expected: 回收站主流程在三种视口通过，跨设备同步专项通过。

## Task 8：完成真实清理、故障恢复和受影响回归

**Files:**
- Modify: `web/e2e/calendar-lifecycle.spec.ts`
- Modify: `server/tests/web_environment.py`
- Modify: `server/tests/test_calendar_lifecycle.py`
- Modify: `server/tests/test_calendar_lifecycle_cleanup.py`

- [ ] **Step 1: 扩展真实 E2E 环境**

`web_environment.py` 为每个 Playwright project 启动：

- 独立 PostgreSQL 测试库和 API；
- Mailpit 账号闭环；
- 现有受控 ICS HTTP 上游；
- `itime.calendar.subscription_worker`；
- 可由测试触发一次的 `itime.calendar.cleanup_worker --once`。

清理入口只允许当前隔离库，不能连接开发库；进程退出时终止所有 worker、删除临时文件和测试
数据库。

- [ ] **Step 2: 增加到期与故障恢复浏览器场景**

通过受控测试 API 或测试数据库辅助把当前账号对象期限推进到过去，再从页面触发/等待一次清理，
验证：

- 回收项从可恢复列表消失且恢复请求返回 410；
- 原名称、地点、URL token 不出现在 API、导出、页面或错误消息；
- 删除响应丢失后按同 operation ID 对账，不产生第二期限；
- 清理 worker 暂停时回收对象仍可恢复；恢复 worker 后只清理已到期对象；
- 订阅上游超时期间删除，迟到响应不会使来源重新出现在日历；
- 页面重载、离开再进入和另一浏览器上下文保持权威状态。

- [ ] **Step 3: 执行专项服务与受影响浏览器回归**

```bash
cd server
uv run pytest -q tests/test_calendar_lifecycle_models.py \
  tests/test_calendar_lifecycle.py tests/test_calendar_lifecycle_cleanup.py \
  tests/test_calendar_foundation.py tests/test_calendar_series.py \
  tests/test_calendar_exceptions.py tests/test_calendar_additions.py \
  tests/test_calendar_imports.py tests/test_calendar_screenshot_imports.py \
  tests/test_calendar_subscription_exceptions.py \
  tests/test_calendar_subscriptions.py \
  tests/test_plan_source_conflicts.py tests/test_planning_inputs.py \
  tests/test_review_sessions.py tests/test_data_export_snapshot.py \
  tests/test_data_export_archive.py tests/test_sync_event_coverage.py \
  tests/test_sync_feed.py tests/test_account_deletion_worker.py

cd ../web
pnpm build
pnpm exec playwright test e2e/calendar-lifecycle.spec.ts \
  e2e/calendar.spec.ts e2e/calendar-imports.spec.ts \
  e2e/calendar-subscriptions.spec.ts e2e/planning.spec.ts \
  e2e/data-exports.spec.ts e2e/sync.spec.ts
```

Expected: 所有专项服务测试、生产构建和受影响 Chromium 三视口流程通过。

- [ ] **Step 4: 人工检查视觉证据**

查看：

```text
/tmp/itime-ui-evidence/desktop-calendar-lifecycle-trash.png
/tmp/itime-ui-evidence/mobile-calendar-lifecycle-trash.png
/tmp/itime-ui-evidence/tablet-calendar-lifecycle-trash.png
/tmp/itime-ui-evidence/desktop-calendar-lifecycle-subscription-restored.png
/tmp/itime-ui-evidence/desktop-calendar-lifecycle-320-large-dark.png
```

确认无空白画面、标题/URL/日期裁切、文字重叠、按钮移位、不可见焦点或根级横向滚动。截图只作
本地证据，不提交仓库。

- [ ] **Step 5: 提交真实 E2E 与环境扩展**

```bash
git add web/e2e/calendar-lifecycle.spec.ts \
  server/tests/web_environment.py \
  server/tests/test_calendar_lifecycle.py \
  server/tests/test_calendar_lifecycle_cleanup.py
git commit -m "test: 验证日历回收完整流程"
```

Expected: 测试提交只包含真实动态验证与隔离环境支持。

## Task 9：执行完整门禁并同步交付证据

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `designer.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/设计/iTime-时间课表与重复规则设计.md`
- Modify: `doc/设计/iTime-ICS订阅设计.md`
- Modify: `doc/设计/iTime-日历生命周期设计.md`
- Modify: `doc/计划/iTime-开发总计划.md`
- Modify: `doc/计划/iTime-M6日历生命周期实施计划.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `web/src/pages/PrivacyPage.vue`
- Modify: `web/src/pages/TermsPage.vue`

- [ ] **Step 1: 执行服务端完整门禁**

```bash
cd server
uv run ruff check src tests
uv run pytest -q
uv build
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run python -m itime.calendar.cleanup_worker --once
```

Expected: Ruff、全量 pytest、sdist/wheel、迁移 head/check 和独立清理进程全部通过。记录真实测试
数量、耗时、迁移 revision 和构建产物名。

- [ ] **Step 2: 执行完整 Chromium 门禁**

```bash
cd web
pnpm build
pnpm exec playwright test
```

Expected: 全部 desktop/mobile/tablet Chromium 用例通过；仅记录配置中明确且有理由的 skip。任何
失败都先定位并修复或证明为既有非确定问题后空载复跑，不能直接写“通过”。

- [ ] **Step 3: 验证运行态和环境清理**

```bash
curl -fsS http://127.0.0.1:8000/api/ready
curl -fsS http://127.0.0.1:8000/api/openapi.json \
  | jq -e '.paths["/api/v1/calendar/series/{series_id}/restore"]'
curl -fsS http://127.0.0.1:8000/api/openapi.json \
  | jq -e '.paths["/api/v1/calendar/subscriptions/{source_id}/restore"]'
```

重启开发 API、订阅 worker、日历 cleanup worker 和 Vite，使
`http://localhost:5173/calendar` 可供用户继续验证。检查：

```sql
SELECT datname FROM pg_database WHERE datname LIKE 'itime_test_%';
```

Expected: `/api/ready` 200、两个恢复路由存在、无测试数据库、无测试 worker、无受控上游或临时
导出残留。

- [ ] **Step 4: 同步文档与公开说明**

文档只写实际结果：

- `REQ-DATA-01` 更新为任务、困难、记忆、日历系列和订阅源具备 30 天回收；
- 隐私页把“日历对象回收站待实现”改为实际已实现范围；
- 明确学期、项目、AI 会话等其他独立对象仍未开放回收；
- README 增加 `python -m itime.calendar.cleanup_worker`；
- 设计和测试记录实际迁移、路由、锁顺序、清理范围、测试命令、数量、耗时和截图位置；
- M6 只收口本模块，不把备份恢复删除台账、语音、Edge/真机、正式模型质量、性能或生产部署
 写成完成。

把本计划所有已执行 checkbox 改为 `[x]`。

- [ ] **Step 5: 检查、提交并确认工作树**

```bash
git diff --check
git status --short
git diff --stat
git diff --cached --name-only
git commit -m "docs: 记录日历生命周期实施证据"
git status --short --branch
git log -12 --oneline
```

Expected: 文档与公开说明提交后工作树干净；提交链至少分别包含模型、系列、订阅、清理、跨模块、
Web 直接操作、Web 回收站、真实 E2E 和证据，不混入凭据、测试数据库、截图或无关文件。
