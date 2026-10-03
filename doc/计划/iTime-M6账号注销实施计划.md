# iTime M6 账号注销实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付普通用户自主申请注销、7 天凭据恢复、在途任务停止、到期完整清理和 Web 可达交互。

**Architecture:** 使用独立 `account_deletions` 持久状态机记录申请、租约、撤销和完成回执，
`users.status=pending_deletion` 作为所有现有 API 的统一阻断点。申请事务先锁用户并终止账号级
在途工作，清理 worker 到期后在单事务中删除用户根行并依靠已验证外键级联；Web 在数据管理页
完成高风险确认，在登录页以正确凭据恢复。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、PostgreSQL 18、Alembic、Argon2、
Vue 3、TypeScript、Lucide、IndexedDB、BroadcastChannel、Playwright Chromium。

---

## 文件结构

服务端：

- `server/src/itime/account_deletion/__init__.py`：账号注销领域包入口。
- `server/src/itime/account_deletion/models.py`：注销作业、租约和最小回执。
- `server/src/itime/account_deletion/schemas.py`：申请和凭据恢复输入。
- `server/src/itime/account_deletion/quiesce.py`：停止 AI、导出、订阅、专注、邮件和临时媒体。
- `server/src/itime/account_deletion/service.py`：申请、撤销、领取、失败退避和最终清理。
- `server/src/itime/account_deletion/router.py`：本人申请与公开凭据恢复 API。
- `server/src/itime/account_deletion/worker.py`：常驻及 `--once` 清理入口。
- `server/src/itime/migrations/versions/0029_account_deletions.py`：状态、表、索引和约束。
- `server/tests/test_account_deletion_models.py`：迁移、ORM 和状态约束。
- `server/tests/test_account_deletion_api.py`：凭据、会话、并发和在途冻结。
- `server/tests/test_account_deletion_worker.py`：租约、全域清理、回滚和双 worker。

Web：

- `web/src/lib/account-deletion.ts`：申请与撤销客户端合同。
- `web/src/components/AccountDeletionSettings.vue`：数据管理危险操作区。
- `web/src/pages/AuthPage.vue`：正确密码后的注销状态及撤销入口。
- `web/src/lib/auth.ts`：跨标签清理当前账号全部本地数据。
- `web/e2e/account-deletion.spec.ts`：申请、第二设备失效、撤销、到期清理和多视口。

## Task 1：建立注销状态、作业模型与迁移

**Files:**
- Create: `server/src/itime/account_deletion/__init__.py`
- Create: `server/src/itime/account_deletion/models.py`
- Create: `server/src/itime/migrations/versions/0029_account_deletions.py`
- Modify: `server/src/itime/auth/models.py`
- Modify: `server/src/itime/ai/models.py`
- Modify: `server/tests/test_planning_models.py`
- Create: `server/tests/test_account_deletion_models.py`

- [x] **Step 1: 写迁移和约束失败测试**

测试在独立 PostgreSQL 数据库中：

```python
def test_account_deletion_migration_constraints_and_downgrade(admin_url):
    with isolated_database(admin_url) as database_url:
        with psycopg.connect(psycopg_url(database_url)) as connection:
            owner_id = seed_user(connection)
            operation_id = uuid4()
            deletion_id = uuid4()
            connection.execute(
                """
                INSERT INTO account_deletions (
                    id, user_id, subject_digest, operation_id, status,
                    requested_at, purge_after, available_at, request_id
                ) VALUES (%s, %s, %s, %s, 'pending', now(),
                          now() + interval '7 days', now(), %s)
                """,
                (deletion_id, owner_id, "a" * 64, operation_id, str(uuid4())),
            )
            connection.execute(
                "UPDATE users SET status='pending_deletion' WHERE id=%s",
                (owner_id,),
            )
            connection.commit()

            assert connection.execute(
                "SELECT status FROM users WHERE id=%s", (owner_id,)
            ).fetchone() == ("pending_deletion",)
            with pytest.raises(CheckViolation):
                with connection.transaction():
                    connection.execute(
                        "UPDATE account_deletions SET purge_after=requested_at WHERE id=%s",
                        (deletion_id,),
                    )
```

另验证：

- `subject_digest` 恰为 64 个小写十六进制字符。
- 同一用户不能同时存在两个 `pending/processing` 作业。
- `processing` 必须有 lease，`completed` 必须无 `user_id` 且有 `completed_at`。
- `cancelled` 可在用户最终被删除后由 `SET NULL` 清除 `user_id`。
- 有任意注销记录时降级拒绝；删除记录后可降到 `0028_sync_feed` 再升级。
- 临时媒体 `deletion_reason=account_deletion` 合法。

- [x] **Step 2: 运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_account_deletion_models.py
```

Expected: FAIL，迁移和模型不存在。

- [x] **Step 3: 实现 ORM**

`AccountDeletion` 固定核心字段：

```python
class AccountDeletion(Base):
    __tablename__ = "account_deletions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    subject_digest: Mapped[str] = mapped_column(String(64))
    operation_id: Mapped[UUID] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(String(16))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_error_message: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

模型增加状态/时间一致性约束、due 索引、`subject_digest` 索引，以及
`user_id WHERE status IN ('pending','processing')` 部分唯一索引。
`User.status` 检查与类型扩展到 `pending_deletion`；`TemporaryMedia.deletion_reason` 扩展
`account_deletion`。

- [x] **Step 4: 实现迁移**

迁移 revision 为 `0029_account_deletions`，down revision 为 `0028_sync_feed`。upgrade 顺序：

1. 替换 `ck_users_status`。
2. 替换 `ck_temporary_media_deletion_reason`。
3. 创建 `account_deletions`、检查约束和索引。

downgrade 先执行：

```sql
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM account_deletions) THEN
    RAISE EXCEPTION 'account_deletions must be empty before downgrade';
  END IF;
END $$;
```

然后删除表并恢复 `0028` 的两个检查约束。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_account_deletion_models.py tests/test_planning_models.py
uv run ruff check src/itime/account_deletion src/itime/auth/models.py \
  src/itime/ai/models.py tests/test_account_deletion_models.py
uv run alembic upgrade head
uv run alembic current
uv run alembic check
git add src/itime/account_deletion src/itime/auth/models.py src/itime/ai/models.py \
  src/itime/migrations/versions/0029_account_deletions.py \
  tests/test_account_deletion_models.py tests/test_planning_models.py
git commit -m "feat: 建立账号注销生命周期"
```

Expected: PASS；head 为 `0029_account_deletions`，ORM 无漂移。

## Task 2：终止账号在途工作与临时副本

**Files:**
- Create: `server/src/itime/account_deletion/quiesce.py`
- Modify: `server/src/itime/ai/job_service.py`
- Modify: `server/src/itime/data_export/service.py`
- Modify: `server/src/itime/calendar/subscription_service.py`
- Modify: `server/src/itime/execution/service.py`
- Modify: `server/src/itime/auth/mail.py`
- Test: `server/tests/test_account_deletion_api.py`
- Test: `server/tests/test_ai_jobs.py`
- Test: `server/tests/test_calendar_subscriptions.py`
- Test: `server/tests/test_data_export_worker.py`

- [x] **Step 1: 写账号冻结和晚到结果失败测试**

构造同一账号的：

- queued 与 running AI 作业、running attempt、正文日志和 `reserved` 额度。
- queued/running/succeeded 导出作业。
- running ICS refresh。
- running 与 paused 专注。
- 未使用验证/重设令牌和未发送邮件。
- 尚有密文的临时媒体。

调用 `quiesce_account(session, user, requested_at)` 后断言：

```python
assert all(job.status == "cancelled" for job in ai_jobs)
assert all(reservation.status == "released" for reservation in reservations)
assert active_export.status == "failed"
assert ready_export.status == "expired"
assert ready_export.artifact_ciphertext is None
assert subscription.refresh_status == "idle"
assert refresh.status == "stale"
assert focus.status == "abandoned"
assert media.encrypted_content is None
assert media.deletion_reason == "account_deletion"
assert token.used_at is not None
assert outbox.encrypted_message is None
```

保存旧 AI/export/subscription claim，事务提交后调用各完成函数，断言返回 `False/None` 且不产生
回复、提案、报告、样本、扣额、导出密文或订阅投影。

- [x] **Step 2: 为三类 worker 增加 active 用户门禁**

`claim_next_job`、`claim_next_export` 和 `claim_next_subscription` 查询分别连接 `User` 并增加：

```python
select(AIJob).join(User, User.id == AIJob.owner_id)
    .where(User.status == "active")
    .with_for_update(skip_locked=True, of=AIJob)

select(DataExportJob).join(User, User.id == DataExportJob.owner_id)
    .where(User.status == "active")
    .with_for_update(skip_locked=True, of=DataExportJob)

select(CalendarSubscription).join(
    User, User.id == CalendarSubscription.owner_id
).where(User.status == "active").with_for_update(
    skip_locked=True, of=CalendarSubscription
)
```

只锁作业/订阅行，不锁用户行，避免与注销事务的 `user → work` 顺序形成死锁。现有 lease 和状态
二次校验保持不变。

- [x] **Step 3: 增加领域终止 helper**

在既有服务文件提供以下事务内 helper：

```python
async def cancel_active_ai_jobs_for_owner(
    session: AsyncSession, owner_id: UUID
) -> int

async def expire_exports_for_account_deletion(
    session: AsyncSession, owner_id: UUID, at: datetime
) -> int

async def invalidate_subscription_refreshes_for_account_deletion(
    session: AsyncSession, owner_id: UUID, at: datetime
) -> int

async def abandon_open_focus_for_account_deletion(
    session: AsyncSession, owner_id: UUID, at: datetime
) -> int

async def invalidate_account_mail(
    session: AsyncSession, owner_id: UUID, at: datetime
) -> int

async def clear_temporary_media_for_account_deletion(
    session: AsyncSession, owner_id: UUID, at: datetime
) -> int
```

AI helper逐个复用 `cancel_job`、`cancel_conversation_projection` 和 `release_reservation`；账号单次
最多三个 user-quota 活动作业，平台截图作业也按查询结果有界处理。专注 helper 调用
`apply_transition(focus, "abandon", at)`，追加 `FocusEvent` 与 `ChangeEvent`。

- [x] **Step 4: 编排 quiesce**

```python
@dataclass(frozen=True)
class QuiesceSummary:
    ai_jobs: int
    exports: int
    subscription_refreshes: int
    focus_sessions: int
    emails: int
    temporary_media: int


async def quiesce_account(
    session: AsyncSession,
    owner_id: UUID,
    *,
    requested_at: datetime,
) -> QuiesceSummary:
    return QuiesceSummary(
        ai_jobs=await cancel_active_ai_jobs_for_owner(session, owner_id),
        exports=await expire_exports_for_account_deletion(
            session, owner_id, requested_at
        ),
        subscription_refreshes=(
            await invalidate_subscription_refreshes_for_account_deletion(
                session, owner_id, requested_at
            )
        ),
        focus_sessions=await abandon_open_focus_for_account_deletion(
            session, owner_id, requested_at
        ),
        emails=await invalidate_account_mail(
            session, owner_id, requested_at
        ),
        temporary_media=await clear_temporary_media_for_account_deletion(
            session, owner_id, requested_at
        ),
    )
```

固定调用顺序为 AI → export → subscription → focus → mail → media，所有数据库变化使用调用方
事务。返回值只含数量，用于用户确认，不写普通日志。

- [x] **Step 5: 回归并提交**

```bash
cd server
uv run pytest -q tests/test_account_deletion_api.py \
  tests/test_ai_jobs.py tests/test_calendar_subscriptions.py \
  tests/test_data_export_worker.py tests/test_focus_sessions.py tests/test_auth.py
uv run ruff check src/itime/account_deletion src/itime/ai/job_service.py \
  src/itime/data_export/service.py src/itime/calendar/subscription_service.py \
  src/itime/execution/service.py src/itime/auth/mail.py
git add src/itime/account_deletion/quiesce.py src/itime/ai/job_service.py \
  src/itime/data_export/service.py src/itime/calendar/subscription_service.py \
  src/itime/execution/service.py src/itime/auth/mail.py \
  tests/test_account_deletion_api.py tests/test_ai_jobs.py \
  tests/test_calendar_subscriptions.py tests/test_data_export_worker.py
git commit -m "feat: 停止注销账号在途工作"
```

## Task 3：提供本人注销申请 API

**Files:**
- Create: `server/src/itime/account_deletion/schemas.py`
- Create: `server/src/itime/account_deletion/service.py`
- Create: `server/src/itime/account_deletion/router.py`
- Modify: `server/src/itime/auth/session.py`
- Modify: `server/src/itime/main.py`
- Test: `server/tests/test_account_deletion_api.py`

- [x] **Step 1: 写申请失败和成功测试**

覆盖：

- 未认证、CSRF 错误、密码错误、邮箱大小写/空格以规范化值比较。
- 邮箱不匹配不改变账号，不返回正确邮箱。
- 管理员账号拒绝。
- 成功返回固定 7 天 UTC 到期时刻、撤销所有设备、清理 cookie 和在途摘要。
- 其他设备立即 401，所有业务写入拒绝。
- 两个并发申请只有一个作业；业务写与申请按用户锁顺序得到完整结果。
- 响应未知后用正确密码登录能恢复同一待注销状态。

输入：

```python
class AccountDeletionRequest(Input):
    operation_id: UUID
    email_confirmation: EmailStr
    current_password: Password

    @field_validator("email_confirmation", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value
```

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_account_deletion_api.py -k request
```

Expected: FAIL，路由尚未注册。

- [x] **Step 3: 实现申请服务**

把 `invalid_credentials` 从 `auth/router.py` 移到 `auth/session.py` 供登录和注销恢复共同使用；
在 `account_deletion/service.py` 增加 `revoke_all_sessions(session, owner_id, at)`，以单条
`UPDATE` 撤销该账号所有未撤销设备会话。

```python
ACCOUNT_DELETION_GRACE = timedelta(days=7)


async def request_account_deletion(
    request: Request,
    *,
    user_id: UUID,
    password_hash: str,
    data: AccountDeletionRequest,
) -> dict:
    security = request.app.state.security
    if not await security.verify_password(
        data.current_password.get_secret_value(), password_hash
    ):
        raise invalid_credentials()
    async with authorized(request, write=True) as identity:
        user = identity.user
        if user.password_hash != password_hash:
            raise AppError("credentials_changed", "密码已变化，请重新登录。", status=409)
        if data.email_confirmation != user.email:
            raise AppError("account_confirmation_mismatch", "邮箱确认不匹配。", status=400)
        if user.is_admin:
            raise AppError("admin_account_protected", "管理员账号不能在此注销。", status=409)
        current = now()
        deletion = AccountDeletion(
            user_id=user.id,
            subject_digest=security.keyed_digest(f"account-deletion:{user.id}"),
            operation_id=data.operation_id,
            status="pending",
            requested_at=current,
            purge_after=current + ACCOUNT_DELETION_GRACE,
            available_at=current + ACCOUNT_DELETION_GRACE,
            request_id=request.state.request_id,
        )
        identity.session.add(deletion)
        summary = await quiesce_account(
            identity.session, user.id, requested_at=current
        )
        user.status = "pending_deletion"
        user.auth_epoch += 1
        user.management_version += 1
        await revoke_all_sessions(identity.session, user.id, at=current)
        audit(request, identity.session, user, "account_deletion_requested", deletion.id)
        await identity.session.flush()
        return account_deletion_result(deletion, summary=summary)
```

真实实现把重复约束冲突转为稳定 `account_deletion_pending`，不回显 SQL。

- [x] **Step 4: 注册路由和 cookie 清理**

```python
@router.post("", status_code=202)
async def request_deletion(
    data: AccountDeletionRequest,
    request: Request,
    response: Response,
) -> dict:
    async with authorized(request) as identity:
        user_id = identity.user.id
        password_hash = identity.user.password_hash
    result = await request_account_deletion(
        request,
        user_id=user_id,
        password_hash=password_hash,
        data=data,
    )
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, samesite="lax")
    return result
```

路由前缀为 `/api/v1/account-deletion`，在 `main.py` 注册。成功响应不包含邮箱、密码摘要、
subject digest 或 lease。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_account_deletion_api.py -k request
uv run ruff check src/itime/account_deletion src/itime/main.py \
  tests/test_account_deletion_api.py
git add src/itime/account_deletion src/itime/main.py \
  tests/test_account_deletion_api.py
git commit -m "feat: 提供本人账号注销申请"
```

## Task 4：实现登录识别、密码找回与撤销注销

**Files:**
- Modify: `server/src/itime/account_deletion/schemas.py`
- Modify: `server/src/itime/account_deletion/service.py`
- Modify: `server/src/itime/account_deletion/router.py`
- Modify: `server/src/itime/auth/router.py`
- Modify: `server/src/itime/auth/session.py`
- Test: `server/tests/test_account_deletion_api.py`
- Test: `server/tests/test_auth.py`

- [x] **Step 1: 写恢复路径失败测试**

覆盖：

- 错误密码始终 `invalid_credentials`，不泄露注销状态。
- 正确密码登录 pending 账号返回 409、申请/到期时刻及 `can_cancel=true`，不设置 cookie。
- 到期边界返回 `can_cancel=false`；撤销端点返回 410。
- 撤销前错误邮箱/密码不改变状态；成功后只有一个新设备会话。
- 撤销保留任务等 durable 数据，AI/导出/临时媒体/专注不复活。
- pending 账号能收到重设邮件并改密，但重设后仍需明确撤销；旧设备保持失效。
- suspended 与 pending 状态错误不混淆。

- [x] **Step 2: 实现待注销登录结果**

在密码正确并锁定用户后，`login` 在 `check_user` 前调用：

```python
if user.status == "pending_deletion":
    raise await pending_deletion_error(session, user.id)
```

`pending_deletion_error` 读取唯一 `pending/processing` 作业并返回：

```python
AppError(
    "account_deletion_pending",
    "账号正在注销，验证后可在期限内撤销。",
    status=409,
    details={
        "requested_at": deletion.requested_at.isoformat(),
        "purge_after": deletion.purge_after.isoformat(),
        "can_cancel": deletion.status == "pending" and now() < deletion.purge_after,
    },
)
```

状态与作业不一致返回 503 `account_deletion_state_invalid`，不静默恢复账号。

- [x] **Step 3: 实现凭据撤销**

```python
class AccountDeletionCancel(Input):
    email: EmailStr
    password: Password
    device_name: str = Field(default="网页浏览器", min_length=1, max_length=100)
```

`cancel_account_deletion` 使用登录相同两阶段密码校验，事务内锁用户再锁注销行。只在
`status=pending` 且 `now < purge_after` 时：

```python
deletion.status = "cancelled"
deletion.cancelled_at = current
deletion.lease_id = None
deletion.lease_until = None
deletion.updated_at = current
user.status = "active"
user.management_version += 1
device, raw = await create_session(request, session, user, data.device_name)
audit(request, session, user, "account_deletion_cancelled", deletion.id)
```

路由设置新 cookie 并返回 `{status: "cancelled", deletion, session}`。

- [x] **Step 4: 允许 pending 账号找回密码**

`forgot-password` 对 `active/pending_deletion` 发 reset 邮件；`reset-password` 对
`pending_deletion` 允许更新密码、递增 auth epoch 和失效 token，但不改变用户状态。成功响应
根据状态返回“密码已重设；账号仍在注销期，请登录并明确撤销”。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_account_deletion_api.py tests/test_auth.py
uv run ruff check src/itime/account_deletion src/itime/auth \
  tests/test_account_deletion_api.py tests/test_auth.py
git add src/itime/account_deletion src/itime/auth/router.py \
  src/itime/auth/session.py tests/test_account_deletion_api.py tests/test_auth.py
git commit -m "feat: 支持撤销账号注销"
```

## Task 5：实现到期清理 worker 和最小回执

**Files:**
- Modify: `server/src/itime/account_deletion/service.py`
- Create: `server/src/itime/account_deletion/worker.py`
- Create: `server/tests/test_account_deletion_worker.py`
- Modify: `server/tests/test_account_deletion_models.py`

- [x] **Step 1: 写清理、并发和回滚失败测试**

在一个账号下创建每个当前含 `owner_id` 的领域数据和直接用户外键数据，并另建对照账号与平台
配置。将 `purge_after` 调到过去，覆盖：

```python
async def run_workers():
    return await asyncio.gather(
        process_one(first_database, settings),
        process_one(second_database, settings),
    )


results = asyncio.run(run_workers())
assert sorted(results) == [False, True]
```

验证：

- 用户、邮箱、密码摘要、设备、正文、URL、密文、样本、额度、同步和所有 owner 行为零。
- 该邮箱的注册邀请删除；用户安全事件删除；管理员事件中的目标和 details 清空。
- 对照账号、注册策略、AI/额度全局配置不变。
- 当前注销作业 `completed/user_id=NULL`，仅保留 HMAC 和时刻。
- 历史 cancelled 作业随用户删除变为 `user_id=NULL`。
- 删除中注入异常后所有数据仍在，作业回到 pending、记录稳定错误并退避。
- stale lease 可重领；到期前、已取消或 lease 不匹配均不清理。
- 清理完成后错误密码登录仍为 `invalid_credentials`，同邮箱可注册全新 UUID。

- [x] **Step 2: 实现领取和失败退避**

```python
@dataclass(frozen=True)
class ClaimedDeletion:
    id: UUID
    user_id: UUID
    lease_id: UUID
    attempt_number: int


async def claim_next_deletion(
    database: Database, *, lease_seconds: int = 120
) -> ClaimedDeletion | None


async def record_deletion_failure(
    database: Database,
    claim: ClaimedDeletion,
    *,
    code: str = "account_deletion_failed",
    message: str = "账号数据清理失败，系统会自动重试。",
) -> bool
```

claim 只锁注销行并立即提交；`available_at` 退避为
`min(3600, 2 ** min(attempt_count, 12))` 秒。错误记录不保存异常正文。

- [x] **Step 3: 实现单事务清理**

```python
async def purge_claimed_account(
    database: Database,
    claim: ClaimedDeletion,
) -> bool:
    async with database.transaction() as session:
        user = await session.scalar(
            select(User).where(User.id == claim.user_id).with_for_update()
        )
        deletion = await session.scalar(
            select(AccountDeletion)
            .where(AccountDeletion.id == claim.id)
            .with_for_update()
        )
        current = now()
        if (
            user is None
            or user.status != "pending_deletion"
            or deletion is None
            or deletion.user_id != user.id
            or deletion.status != "processing"
            or deletion.lease_id != claim.lease_id
            or deletion.lease_until is None
            or deletion.lease_until <= current
            or deletion.purge_after > current
        ):
            return False
        email = user.email
        await scrub_account_audit(session, user.id)
        await session.execute(
            delete(RegistrationInvitation).where(
                or_(
                    RegistrationInvitation.email == email,
                    RegistrationInvitation.consumed_by_user_id == user.id,
                )
            )
        )
        await session.delete(user)
        await session.flush()
        deletion.user_id = None
        deletion.status = "completed"
        deletion.lease_id = None
        deletion.lease_until = None
        deletion.completed_at = now()
        deletion.updated_at = deletion.completed_at
        return True
```

`scrub_account_audit` 删除 `SecurityEvent.user_id=user_id`，并对管理员事件
`target_id=str(user_id)` 设置 `target_id=NULL, details={}`。

- [x] **Step 4: 实现 worker**

```python
async def run(*, once: bool = False) -> None:
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    try:
        while True:
            claim = None
            try:
                claim = await claim_next_deletion(database)
                processed = (
                    await purge_claimed_account(database, claim)
                    if claim is not None else False
                )
            except Exception as exc:
                logger.warning(
                    "account_deletion_worker_error",
                    extra={"error_type": type(exc).__name__},
                )
                if claim is not None:
                    await record_deletion_failure(database, claim)
                if once:
                    raise RuntimeError("账号注销清理失败，请检查数据库。") from None
                processed = False
            if once:
                break
            if not processed:
                await asyncio.sleep(5)
    finally:
        await database.close()
```

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_account_deletion_worker.py \
  tests/test_account_deletion_models.py
uv run python -m itime.account_deletion.worker --once
uv run ruff check src/itime/account_deletion tests/test_account_deletion_worker.py
git add src/itime/account_deletion/worker.py \
  src/itime/account_deletion/service.py \
  tests/test_account_deletion_worker.py tests/test_account_deletion_models.py
git commit -m "feat: 清理到期注销账号"
```

## Task 6：接入后台状态和本人导出

**Files:**
- Modify: `server/src/itime/admin/management.py`
- Modify: `server/src/itime/data_export/snapshot.py`
- Modify: `server/tests/test_admin_management.py`
- Modify: `server/tests/test_data_export_snapshot.py`

- [x] **Step 1: 写后台和导出失败测试**

验证管理员列表可按 `pending_deletion` 筛选并显示“注销中”，但状态修改端点对该账号返回
409 `account_deletion_pending`。取消过一次注销后，完整 JSON 的账号分类包含：

```json
{
  "account_deletions": [
    {
      "id": "uuid",
      "status": "cancelled",
      "requested_at": "timestamp",
      "purge_after": "timestamp",
      "cancelled_at": "timestamp"
    }
  ]
}
```

导出不包含 `subject_digest`、`operation_id`、lease、request ID 或内部错误。

- [x] **Step 2: 实现后台只读状态**

将后台查询 schema 扩展为：

```python
status: Literal["all", "active", "suspended", "pending_deletion"] = "all"
```

`user_result` 保持稳定 `status` 字段；`locked_managed_user` 在
`user.status == "pending_deletion"` 时返回 409，管理员不能代替撤销、延长或停用。
Web 管理类型和文案在 Task 8 同步。

- [x] **Step 3: 扩展完整 JSON 快照**

在账号快照查询 `AccountDeletion.user_id == owner_id`，仅输出白名单字段：
`id/status/requested_at/purge_after/cancelled_at/created_at/updated_at`。只导出取消历史；
活动注销账号没有业务访问，因此不会创建新导出。

- [x] **Step 4: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_admin_management.py tests/test_data_export_snapshot.py \
  tests/test_account_deletion_api.py
uv run ruff check src/itime/admin/management.py \
  src/itime/data_export/snapshot.py tests/test_admin_management.py \
  tests/test_data_export_snapshot.py
git add src/itime/admin/management.py src/itime/data_export/snapshot.py \
  tests/test_admin_management.py tests/test_data_export_snapshot.py
git commit -m "feat: 展示账号注销生命周期"
```

## Task 7：实现 Web 注销客户端和跨标签本地清理

**Files:**
- Create: `web/src/lib/account-deletion.ts`
- Modify: `web/src/lib/auth.ts`
- Modify: `web/src/pages/AuthPage.vue`
- Test: `web/e2e/account-deletion.spec.ts`

- [x] **Step 1: 写登录恢复和本地清理失败 E2E**

使用两个 browser context 登录同一账号，预置：

- IndexedDB 加密缓存。
- 任务步骤待传队列。
- 专注待传队列。
- 两个标签各自的 session 游标。

申请注销后断言两个标签均清空内存身份、游标和账号本地数据，第二设备业务请求 401。错误密码
登录只显示凭据错误；正确密码显示到期时刻和撤销按钮；撤销成功显示恢复完成并可进入 `/ai`。

- [x] **Step 2: 实现 API 类型**

```typescript
export interface AccountDeletion {
  id: string
  status: 'pending' | 'processing' | 'cancelled' | 'completed'
  requested_at: string
  purge_after: string
  cancelled_at: string | null
  completed_at: string | null
}

export function requestAccountDeletion(input: {
  operation_id: string
  email_confirmation: string
  current_password: string
}) {
  return api<{ deletion: AccountDeletion; stopped: Record<string, number> }>(
    '/account-deletion',
    input,
  )
}

export function cancelAccountDeletion(input: {
  email: string
  password: string
  device_name: string
}) {
  return api<{ status: 'cancelled'; deletion: AccountDeletion; session: Session }>(
    '/account-deletion/cancel',
    input,
  )
}
```

- [x] **Step 3: 实现跨标签删除广播**

`auth.ts` 增加：

```typescript
export function discardAccountLocalData(userId: string) {
  clearTaskQueue(userId)
  clearFocusQueues(userId)
  sessionStorage.removeItem(`itime-sync-cursor:${userId}`)
  void clearAccountCache(userId)
}

export function notifyAccountDiscard(userId: string) {
  channel?.postMessage({ type: 'discard', user_id: userId })
}
```

channel 接收 `discard` 时，即使当前标签没有有效身份，也清理该 namespace；若身份属于该账号，
同时 `clearSession({ discardLocal: true })`。保留旧 `changed` 消息兼容现有登录/改密/退出测试。

- [x] **Step 4: 接入登录页撤销**

`AuthPage.vue` 在 login 捕获 `account_deletion_pending` 时保留内存中的 email/password，
从 `details` 构造只读 pending 状态。按钮调用 cancel API；成功后 `acceptSession`、
`notifySessionChange`，清空密码并显示恢复完成视图，用户点击“进入 iTime”到 `/ai`。

组件卸载和任何非 pending 错误都清空注销状态；不得把凭据或状态写 URL/Storage。

- [x] **Step 5: 构建、验证并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/account-deletion.spec.ts --grep "restore|local data"
git add src/lib/account-deletion.ts src/lib/auth.ts src/pages/AuthPage.vue \
  e2e/account-deletion.spec.ts
git commit -m "feat: 恢复待注销账号"
```

## Task 8：构建数据管理注销界面和后台状态

**Files:**
- Create: `web/src/components/AccountDeletionSettings.vue`
- Modify: `web/src/pages/AccountPage.vue`
- Modify: `web/src/components/AdminUserManagement.vue`
- Modify: `web/src/lib/admin-quota.ts`
- Modify: `web/src/pages/PrivacyPage.vue`
- Modify: `web/src/pages/TermsPage.vue`
- Modify: `web/src/style.css`
- Test: `web/e2e/account-deletion.spec.ts`
- Test: `web/e2e/admin-management.spec.ts`
- Test: `web/e2e/public.spec.ts`

- [ ] **Step 1: 写危险操作和响应式失败 E2E**

覆盖：

- `/account/data` 先显示导出，再显示未嵌套卡片的“注销账号”危险区。
- 打开对话框、Escape/取消、错误邮箱、错误密码、提交中禁用。
- 本地有待同步项时显示准确数量并要求额外确认；取消不调用 API。
- 成功显示 7 天截止并退出；第二设备失效。
- 管理后台显示“注销中”且无启用/停用按钮。
- 隐私页把账号注销改为“已实现”，协议不再声称删除/导出全部待实现。
- desktop/mobile/tablet、320×700 最大字号深色、键盘、axe、控制台和横向溢出。

- [ ] **Step 2: 实现注销危险区**

组件状态：

```typescript
const values = reactive({
  email: '',
  password: '',
})
const operationId = ref('')
const dialog = ref<HTMLDialogElement | null>(null)
const pendingCount = computed(pendingLocalOperationCount)
```

打开时清空密码并聚焦邮箱；提交前要求 `values.email.trim().toLowerCase() === identity.user.email`。
若待传数非零，再显示原生确认并明确这些操作不会保存。成功后：

```typescript
const userId = identity.value!.user.id
const result = await requestAccountDeletion({
  operation_id: operationId.value || crypto.randomUUID(),
  email_confirmation: values.email,
  current_password: values.password,
})
clearSession({ discardLocal: true })
notifyAccountDiscard(userId)
await router.replace({
  path: '/login',
  state: {
    deletionRequested: true,
    purgeAfter: result.deletion.purge_after,
  },
})
```

未知保存状态保留原 `operation_id`，提示重新登录核对，不重复宣称账号仍活动。

- [ ] **Step 3: 接入数据页和样式**

`AccountPage.vue` 的 data 分支渲染一个无外层卡片的容器，依次包含
`DataExportSettings` 和 `AccountDeletionSettings`。危险区以分隔线、错误色图标和明确按钮表达，
不使用整页红色背景；对话框在窄屏自然换行。

- [ ] **Step 4: 更新后台与公开文案**

`AdminUserManagement.vue`：

- 状态 filter 增加 `pending_deletion`。
- badge 显示“注销中”。
- pending 行不显示停用/恢复按钮，只说明用户可在期限内自行撤销。

`PrivacyPage.vue` 更新当前已实现的规划、复盘、额度、同步、导出和注销事实；账号注销行标记
“已实现：申请后锁定，7 天内可撤销，到期清理”。`TermsPage.vue` 改为按当前实际页面执行，
不再把已实现的导出和注销写成未来能力。

- [ ] **Step 5: 构建、验证并提交**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/account-deletion.spec.ts \
  e2e/admin-management.spec.ts e2e/public.spec.ts
git add src/components/AccountDeletionSettings.vue src/pages/AccountPage.vue \
  src/components/AdminUserManagement.vue src/lib/admin-quota.ts \
  src/pages/PrivacyPage.vue src/pages/TermsPage.vue src/style.css \
  e2e/account-deletion.spec.ts e2e/admin-management.spec.ts e2e/public.spec.ts
git commit -m "feat: 构建账号注销与恢复界面"
```

## Task 9：完成隔离 worker、到期 UI 和全链路证据

**Files:**
- Modify: `server/tests/support_app.py`
- Modify: `server/tests/web_environment.py`
- Modify: `web/e2e/account-deletion.spec.ts`

- [ ] **Step 1: 在隔离环境启动清理 worker**

```python
account_deletion_process = stack.enter_context(process(
    [sys.executable, "-m", "itime.account_deletion.worker"],
    SERVER,
    env,
    logs / "account-deletion.log",
))
```

加入总存活检查，结束时由 `ExitStack` 终止。不得复用开发数据库。

- [ ] **Step 2: 增加测试专用到期控制**

只在 `tests.support_app` 注册受控端点：当前已验证测试账号携带 CSRF 后，可把自己的 pending
作业 `purge_after/available_at` 调到过去。端点不接受任意 user ID，不进入生产 `main.py`。

- [ ] **Step 3: 完成真实 E2E**

主流程：

1. 注册账号，创建任务和可下载导出，打开第二设备。
2. 在 `/account/data` 申请注销并核对本地清理。
3. 第二设备重新加载后回登录；错误密码不泄露状态。
4. 正确密码显示截止；撤销后任务仍在，导出临时文件不再可下载。
5. 再次申请，通过测试端点到期，等待真实 worker 删除。
6. 登录只返回统一凭据错误；数据库探针确认 user/owner/email 行为零。
7. 同邮箱重新注册得到新用户 ID，无法读取旧内容。

截图：

```text
/tmp/itime-ui-evidence/{desktop,mobile,tablet}-account-deletion-dialog.png
/tmp/itime-ui-evidence/{desktop,mobile,tablet}-account-deletion-pending.png
/tmp/itime-ui-evidence/mobile-account-deletion-320-large-dark.png
/tmp/itime-ui-evidence/desktop-admin-account-deletion.png
```

- [ ] **Step 4: 人工检查并提交**

逐张确认非空、截止时间/邮箱/按钮不重叠、危险操作层级清楚、320px 无横向溢出。

```bash
cd web
pnpm exec playwright test e2e/account-deletion.spec.ts
git add ../server/tests/support_app.py ../server/tests/web_environment.py \
  e2e/account-deletion.spec.ts
git commit -m "test: 验证账号注销真实链路"
```

## Task 10：全量回归并同步交付证据

**Files:**
- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `designer.md`
- Modify: `cloud.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/设计/iTime-账号注销设计.md`
- Modify: `doc/计划/iTime-开发总计划.md`
- Modify: `doc/计划/iTime-M6账号注销实施计划.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-产品验收清单.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`

- [ ] **Step 1: 执行服务端专项与受影响回归**

```bash
cd server
uv run pytest -q tests/test_account_deletion_models.py \
  tests/test_account_deletion_api.py tests/test_account_deletion_worker.py
uv run pytest -q tests/test_auth.py tests/test_admin_management.py \
  tests/test_ai_jobs.py tests/test_ai_conversation_worker.py \
  tests/test_planning_worker.py tests/test_review_worker.py \
  tests/test_calendar_subscriptions.py tests/test_data_export_worker.py \
  tests/test_data_export_snapshot.py tests/test_focus_sessions.py
uv run ruff check .
uv run alembic upgrade head
uv run alembic current
uv run alembic check
uv run python -m itime.account_deletion.worker --once
uv build --no-sources
```

Expected: 全部通过；head 为 `0029_account_deletions`，测试库残留为 0。

- [ ] **Step 2: 执行服务端全量回归**

```bash
cd server
uv run pytest -q
```

Expected: 所有真实 PostgreSQL/HTTP 测试通过。

- [ ] **Step 3: 执行 Web 专项、受影响与全量回归**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/account-deletion.spec.ts
pnpm exec playwright test e2e/auth.spec.ts e2e/account-deletion.spec.ts \
  e2e/admin-management.spec.ts e2e/data-exports.spec.ts \
  e2e/privacy-governance.spec.ts e2e/public.spec.ts e2e/sync.spec.ts
pnpm test:e2e
```

Expected: 全部 Chromium projects 通过。

- [ ] **Step 4: 恢复开发运行环境并核对**

重启 API 和新增 worker：

```bash
cd server
uv run alembic upgrade head
uv run uvicorn itime.main:create_app --factory --host 127.0.0.1 \
  --port 8000 --no-access-log
uv run python -m itime.account_deletion.worker
```

另行验证：

```bash
curl -fsS http://127.0.0.1:8000/api/ready
curl -fsS http://127.0.0.1:8000/api/openapi.json | \
  jq -e '.paths["/api/v1/account-deletion"] and
         .paths["/api/v1/account-deletion/cancel"]'
curl -fsSI http://localhost:5173/account/data
docker --context colima-itime compose --env-file .env.local exec -T postgres \
  psql -U itime -d postgres -tAc \
  "SELECT count(*) FROM pg_database WHERE datname LIKE 'itime_test_%';"
```

Expected: API ready、注销路由存在、页面 200、worker 运行、随机测试数据库为 0。

- [ ] **Step 5: 同步实际实现和证据**

- README 和 cloud 增加注销 worker 与 7 天恢复命令/边界。
- 追踪矩阵更新 `REQ-DATA-02`；不得把 M8 备份重放写成已完成。
- 详细设计、测试方案、验收清单、designer 和实施记录写实际代码、命令、数量、耗时和截图。
- 隐私说明保持“已经发送到供应商的数据不能撤回”的事实。
- 其他对象单项回收、困难/任务语音、公开链接、其他 AI 提案、Edge/真机、性能和生产部署继续
  明确为未完成。

- [ ] **Step 6: 检查范围并提交证据**

```bash
git diff --check
git status --short
git diff --cached --name-only
```

确认无 `.env`、Cookie、邮箱 token、密码、导出 ZIP、临时截图、Playwright 输出、数据库文件或
删除前正文进入暂存。

```bash
git add README.md doc/README.md designer.md cloud.md \
  doc/需求/iTime-需求追踪矩阵.md doc/设计/iTime-详细设计文档.md \
  doc/设计/iTime-账号注销设计.md doc/计划/iTime-开发总计划.md \
  doc/计划/iTime-M6账号注销实施计划.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-产品验收清单.md doc/验收/iTime-Web实施与验证.md
git commit -m "docs: 记录账号注销实施证据"
```
