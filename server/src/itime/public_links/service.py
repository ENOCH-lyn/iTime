from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from itime.auth.models import User
from itime.auth.security import now
from itime.database import Database
from itime.public_links.fetcher import PublicLinkFetchResult
from itime.tasks.models import Task, TaskLink
from itime.tasks.service import emit_change

MAX_FETCH_ATTEMPTS = 3
MAX_OWNER_ATTEMPTS_PER_WINDOW = 20
OWNER_RATE_WINDOW = timedelta(minutes=10)
RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5))
OWNER_ADVISORY_LOCK_SEED = 731_924
CLAIM_SCAN_LIMIT = 100


@dataclass(frozen=True)
class ClaimedPublicLink:
    link_id: UUID
    task_id: UUID
    owner_id: UUID
    url: str
    fetch_revision: int
    attempt_number: int
    lease_id: UUID


def public_link_result(link: TaskLink) -> dict:
    source_host = urlsplit(link.url).hostname or ""
    return {
        "id": link.id,
        "title": link.title,
        "url": link.url,
        "position": link.position,
        "fetch": {
            "status": link.fetch_status,
            "source_host": source_host,
            "final_host": link.final_host,
            "page_title": link.page_title,
            "summary": link.summary,
            "fetched_at": link.last_success_at,
            "content_sha256": link.content_sha256,
            "last_attempt_at": link.last_attempt_at,
            "next_attempt_at": (
                link.available_at if link.fetch_status == "queued" else None
            ),
            "using_previous_snapshot": (
                link.last_success_at is not None and link.fetch_status != "ready"
            ),
            "error": (
                {
                    "code": link.last_error_code,
                    "message": link.last_error_message,
                }
                if link.last_error_code is not None
                else None
            ),
        },
    }


async def claim_next_public_link(
    database: Database,
    *,
    lease_seconds: int,
) -> ClaimedPublicLink | None:
    current = now()
    blocked_owners: set[UUID] = set()
    async with database.transaction() as session:
        for _ in range(CLAIM_SCAN_LIMIT):
            candidate = aliased(TaskLink)
            active = aliased(TaskLink)
            statement = (
                select(candidate)
                .join(
                    Task,
                    and_(
                        Task.id == candidate.task_id,
                        Task.owner_id == candidate.owner_id,
                    ),
                )
                .join(User, User.id == candidate.owner_id)
                .where(
                    User.status == "active",
                    Task.archived_at.is_(None),
                    Task.deleted_at.is_(None),
                    Task.purged_at.is_(None),
                    or_(
                        and_(
                            candidate.fetch_status == "queued",
                            candidate.available_at <= current,
                        ),
                        and_(
                            candidate.fetch_status == "running",
                            candidate.lease_until <= current,
                        ),
                    ),
                    ~select(active.id)
                    .where(
                        active.owner_id == candidate.owner_id,
                        active.id != candidate.id,
                        active.fetch_status == "running",
                        active.lease_until > current,
                    )
                    .exists(),
                )
                .order_by(candidate.available_at, candidate.owner_id, candidate.id)
                .limit(1)
                .with_for_update(skip_locked=True, of=candidate)
            )
            if blocked_owners:
                statement = statement.where(
                    candidate.owner_id.not_in(blocked_owners)
                )
            link = await session.scalar(statement)
            if link is None:
                return None
            owner_locked = await session.scalar(
                text(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtextextended(:owner_id, :seed))"
                ),
                {
                    "owner_id": str(link.owner_id),
                    "seed": OWNER_ADVISORY_LOCK_SEED,
                },
            )
            if not owner_locked:
                blocked_owners.add(link.owner_id)
                continue
            recent_attempts = list(
                await session.scalars(
                    select(TaskLink.last_attempt_at)
                    .where(
                        TaskLink.owner_id == link.owner_id,
                        TaskLink.last_attempt_at >= current - OWNER_RATE_WINDOW,
                    )
                    .order_by(TaskLink.last_attempt_at)
                    .limit(MAX_OWNER_ATTEMPTS_PER_WINDOW)
                )
            )
            if len(recent_attempts) >= MAX_OWNER_ATTEMPTS_PER_WINDOW:
                blocked_owners.add(link.owner_id)
                continue
            if link.attempt_count >= MAX_FETCH_ATTEMPTS:
                link.fetch_status = "failed"
                link.lease_id = None
                link.lease_until = None
                link.last_error_code = "public_link_attempts_exhausted"
                link.last_error_message = (
                    "网页多次读取失败，请检查地址后手动重试。"
                )
                link.updated_at = current
                emit_change(
                    session,
                    link.owner_id,
                    "task_link",
                    link.id,
                    "updated",
                    link.fetch_revision,
                )
                continue
            lease_id = uuid4()
            link.fetch_status = "running"
            link.attempt_count += 1
            link.lease_id = lease_id
            link.lease_until = current + timedelta(seconds=lease_seconds)
            link.last_attempt_at = current
            link.updated_at = current
            await session.flush()
            return ClaimedPublicLink(
                link_id=link.id,
                task_id=link.task_id,
                owner_id=link.owner_id,
                url=link.url,
                fetch_revision=link.fetch_revision,
                attempt_number=link.attempt_count,
                lease_id=lease_id,
            )
    return None


async def renew_public_link_lease(
    database: Database,
    claim: ClaimedPublicLink,
    *,
    lease_seconds: int,
) -> bool:
    current = now()
    async with database.transaction() as session:
        link = await _locked_claim_link(session, claim, current=current)
        if link is None:
            return False
        link.lease_until = current + timedelta(seconds=lease_seconds)
        link.updated_at = current
        return True


async def complete_public_link(
    database: Database,
    claim: ClaimedPublicLink,
    result: PublicLinkFetchResult,
) -> bool:
    current = now()
    async with database.transaction() as session:
        link = await _locked_claim_link(session, claim, current=current)
        if link is None:
            return False
        if not await _claim_owner_and_task_active(session, link):
            _invalidate_inactive_link(session, link, current)
            return False
        link.fetch_status = "ready"
        link.lease_id = None
        link.lease_until = None
        link.available_at = current
        link.last_success_at = current
        link.final_host = result.final_host
        link.media_type = result.document.media_type
        link.page_title = result.document.page_title
        link.summary = result.document.summary
        link.extracted_text = result.document.extracted_text
        link.content_sha256 = result.content_sha256
        link.last_error_code = None
        link.last_error_message = None
        link.last_http_status = result.http_status
        link.updated_at = current
        _emit_link_change(session, link)
        return True


async def fail_public_link(
    database: Database,
    claim: ClaimedPublicLink,
    *,
    error_code: str,
    error_message: str,
    retryable: bool,
    http_status: int | None = None,
) -> bool:
    current = now()
    async with database.transaction() as session:
        link = await _locked_claim_link(session, claim, current=current)
        if link is None:
            return False
        if not await _claim_owner_and_task_active(session, link):
            _invalidate_inactive_link(session, link, current)
            return False
        link.lease_id = None
        link.lease_until = None
        link.last_error_code = error_code[:100]
        link.last_error_message = error_message[:500]
        link.last_http_status = http_status
        link.updated_at = current
        if retryable and link.attempt_count < MAX_FETCH_ATTEMPTS:
            link.fetch_status = "queued"
            delay_index = min(link.attempt_count - 1, len(RETRY_DELAYS) - 1)
            link.available_at = current + RETRY_DELAYS[delay_index]
        else:
            link.fetch_status = "failed"
            link.available_at = current
        _emit_link_change(session, link)
        return True


async def invalidate_public_link_fetches_for_account_deletion(
    session: AsyncSession,
    owner_id: UUID,
    at: datetime,
) -> int:
    links = list(
        await session.scalars(
            select(TaskLink)
            .where(
                TaskLink.owner_id == owner_id,
                TaskLink.fetch_status.in_(("queued", "running")),
            )
            .order_by(TaskLink.id)
            .with_for_update()
        )
    )
    for link in links:
        link.fetch_revision += 1
        link.fetch_status = "failed"
        link.lease_id = None
        link.lease_until = None
        link.available_at = at
        link.last_error_code = "public_link_account_deletion"
        link.last_error_message = "账号已申请注销，网页读取已停止。"
        link.last_http_status = None
        link.updated_at = at
        _emit_link_change(session, link)
    return len(links)


async def stop_public_link_fetches_for_task(
    session: AsyncSession,
    task: Task,
    *,
    at: datetime,
) -> int:
    links = list(
        await session.scalars(
            select(TaskLink)
            .where(
                TaskLink.task_id == task.id,
                TaskLink.owner_id == task.owner_id,
                TaskLink.fetch_status.in_(("queued", "running")),
            )
            .order_by(TaskLink.id)
            .with_for_update()
        )
    )
    for link in links:
        link.fetch_revision += 1
        link.fetch_status = "failed"
        link.lease_id = None
        link.lease_until = None
        link.available_at = at
        link.last_error_code = "public_link_task_inactive"
        link.last_error_message = (
            "任务已归档或移到回收站，网页读取已停止。"
        )
        link.last_http_status = None
        link.updated_at = at
        _emit_link_change(session, link)
    return len(links)


async def _locked_claim_link(
    session: AsyncSession,
    claim: ClaimedPublicLink,
    *,
    current: datetime,
) -> TaskLink | None:
    link = await session.scalar(
        select(TaskLink)
        .where(
            TaskLink.id == claim.link_id,
            TaskLink.task_id == claim.task_id,
            TaskLink.owner_id == claim.owner_id,
            TaskLink.fetch_status == "running",
            TaskLink.fetch_revision == claim.fetch_revision,
            TaskLink.attempt_count == claim.attempt_number,
            TaskLink.lease_id == claim.lease_id,
            TaskLink.lease_until > current,
        )
        .with_for_update()
    )
    return link


async def _claim_owner_and_task_active(
    session: AsyncSession,
    link: TaskLink,
) -> bool:
    user_status = await session.scalar(
        select(User.status).where(User.id == link.owner_id)
    )
    task = await session.scalar(
        select(Task).where(
            Task.id == link.task_id,
            Task.owner_id == link.owner_id,
        )
    )
    return bool(
        user_status == "active"
        and task is not None
        and task.archived_at is None
        and task.deleted_at is None
        and task.purged_at is None
    )


def _invalidate_inactive_link(
    session: AsyncSession,
    link: TaskLink,
    current: datetime,
) -> None:
    link.fetch_revision += 1
    link.fetch_status = "failed"
    link.lease_id = None
    link.lease_until = None
    link.available_at = current
    link.last_error_code = "public_link_task_inactive"
    link.last_error_message = "任务当前不可用，网页读取结果未保存。"
    link.last_http_status = None
    link.updated_at = current
    _emit_link_change(session, link)


def _emit_link_change(session: AsyncSession, link: TaskLink) -> None:
    emit_change(
        session,
        link.owner_id,
        "task_link",
        link.id,
        "updated",
        link.fetch_revision,
    )
