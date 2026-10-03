from datetime import date, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.job_service import job_result
from itime.ai.models import AIJob
from itime.calendar.models import UserTimeSettings
from itime.calendar.service import activity_day_at
from itime.errors import AppError
from itime.review.fact_service import collect_review_facts
from itime.review.memory_service import memory_proposal_result
from itime.review.models import (
    MemoryProposal,
    ReviewAnnotation,
    ReviewInputSnapshot,
    ReviewMessage,
    ReviewReportVersion,
    ReviewSession,
)
from itime.review.schemas import (
    ReviewAnnotationCreateInput,
    ReviewAnnotationDeleteInput,
    ReviewAnnotationUpdateInput,
    ReviewCreateInput,
    ReviewFactsUpdateInput,
)
from itime.tasks.service import (
    emit_change,
    replay_operation,
    save_operation,
)

ACTIVE_JOB_STATES = ("queued", "running")


def review_result(review: ReviewSession) -> dict:
    return {
        "id": review.id,
        "activity_date": review.activity_date,
        "time_zone": review.time_zone,
        "supplemental_facts": review.supplemental_facts,
        "input_revision": review.input_revision,
        "question_count": review.question_count,
        "version": review.version,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


def review_message_result(message: ReviewMessage) -> dict:
    return {
        "id": message.id,
        "sequence": message.sequence,
        "role": message.role,
        "kind": message.kind,
        "content": message.content,
        "source_job_id": message.source_job_id,
        "created_at": message.created_at,
    }


def report_summary(report: ReviewReportVersion) -> dict:
    return {
        "id": report.id,
        "version": report.version,
        "summary": report.summary,
        "input_snapshot_id": report.input_snapshot_id,
        "created_at": report.created_at,
    }


def annotation_result(annotation: ReviewAnnotation) -> dict:
    return {
        "id": annotation.id,
        "content": annotation.content,
        "deleted_at": annotation.deleted_at,
        "version": annotation.version,
        "created_at": annotation.created_at,
        "updated_at": annotation.updated_at,
    }


def report_result(report: ReviewReportVersion) -> dict:
    return {
        **report_summary(report),
        "facts": report.facts,
        "user_statements": report.user_statements,
        "insights": report.insights,
        "next_actions": report.next_actions,
        "uncertainties": report.uncertainties,
        "source_job_id": report.source_job_id,
    }


async def _time_settings(
    session: AsyncSession,
    owner_id: UUID,
) -> UserTimeSettings:
    settings = await session.get(UserTimeSettings, owner_id)
    if settings is None:
        raise AppError(
            "time_settings_required",
            "请先保存当前使用的时区，再开始复盘。",
            status=409,
        )
    return settings


async def owned_review(
    session: AsyncSession,
    owner_id: UUID,
    review_id: UUID,
    *,
    lock: bool = False,
) -> ReviewSession:
    statement = select(ReviewSession).where(
        ReviewSession.id == review_id,
        ReviewSession.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    review = await session.scalar(statement)
    if review is None:
        raise AppError("review_not_found", "未找到此复盘。", status=404)
    return review


def review_version_conflict(review: ReviewSession) -> AppError:
    return AppError(
        "review_version_conflict",
        "此复盘已在其他页面或设备更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": review.version},
    )


async def create_or_get_review(
    session: AsyncSession,
    owner_id: UUID,
    data: ReviewCreateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = "review.create"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    settings = await _time_settings(session, owner_id)
    current_activity_date = activity_day_at(
        current_time,
        settings.time_zone,
        start_minute=settings.activity_day_start_minute,
    )
    if data.activity_date > current_activity_date:
        raise AppError(
            "review_date_in_future",
            "不能为未来活动日创建复盘。",
            status=422,
            details={"current_activity_date": current_activity_date.isoformat()},
        )
    review = await session.scalar(
        select(ReviewSession)
        .where(
            ReviewSession.owner_id == owner_id,
            ReviewSession.activity_date == data.activity_date,
        )
        .with_for_update()
    )
    created = review is None
    if review is None:
        review = ReviewSession(
            owner_id=owner_id,
            activity_date=data.activity_date,
            time_zone=settings.time_zone,
            supplemental_facts="",
            input_revision=0,
            question_count=0,
            version=1,
            created_at=current_time,
            updated_at=current_time,
        )
        session.add(review)
        await session.flush()
        emit_change(
            session,
            owner_id,
            "review_session",
            review.id,
            "created",
            review.version,
        )
    result = {
        "operation_id": data.operation_id,
        "created": created,
        "review": review_result(review),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result


async def update_review_facts(
    session: AsyncSession,
    owner_id: UUID,
    review_id: UUID,
    data: ReviewFactsUpdateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"review.facts.update:{review_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    review = await owned_review(session, owner_id, review_id, lock=True)
    if review.version != data.expected_version:
        raise review_version_conflict(review)
    if review.supplemental_facts != data.supplemental_facts:
        review.supplemental_facts = data.supplemental_facts
        review.input_revision += 1
        review.version += 1
        review.updated_at = current_time
        emit_change(
            session,
            owner_id,
            "review_session",
            review.id,
            "updated",
            review.version,
        )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "review": review_result(review),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result


async def review_detail(
    session: AsyncSession,
    owner_id: UUID,
    review_id: UUID,
    *,
    current_time: datetime,
) -> dict:
    review = await owned_review(session, owner_id, review_id)
    facts, facts_sha256, _, _ = await collect_review_facts(
        session,
        owner_id,
        review.activity_date,
        current_time=current_time,
    )
    messages = list(
        await session.scalars(
            select(ReviewMessage)
            .where(
                ReviewMessage.owner_id == owner_id,
                ReviewMessage.review_session_id == review.id,
            )
            .order_by(ReviewMessage.sequence)
        )
    )
    reports = list(
        await session.scalars(
            select(ReviewReportVersion)
            .where(
                ReviewReportVersion.owner_id == owner_id,
                ReviewReportVersion.review_session_id == review.id,
            )
            .order_by(ReviewReportVersion.version.desc())
        )
    )
    active_job = await session.scalar(
        select(AIJob)
        .where(
            AIJob.owner_id == owner_id,
            AIJob.review_session_id == review.id,
            AIJob.status.in_(ACTIVE_JOB_STATES),
        )
        .order_by(AIJob.created_at.desc())
    )
    freshness = "none"
    if reports:
        snapshot = await session.get(
            ReviewInputSnapshot,
            reports[0].input_snapshot_id,
        )
        if snapshot is None:
            freshness = "stale_both"
        else:
            user_changed = (
                snapshot.session_input_revision != review.input_revision
            )
            facts_changed = snapshot.facts_sha256 != facts_sha256
            if user_changed and facts_changed:
                freshness = "stale_both"
            elif user_changed:
                freshness = "stale_user_input"
            elif facts_changed:
                freshness = "stale_business_facts"
            else:
                freshness = "current"
    return {
        "review": review_result(review),
        "facts": facts,
        "facts_sha256": facts_sha256,
        "messages": [review_message_result(message) for message in messages],
        "reports": [report_summary(report) for report in reports],
        "active_job": job_result(active_job) if active_job is not None else None,
        "report_freshness": freshness,
        "server_now": current_time,
    }


async def list_reviews(
    session: AsyncSession,
    owner_id: UUID,
    *,
    before_date: date | None,
    before_id: UUID | None,
    limit: int,
) -> dict:
    if (before_date is None) != (before_id is None):
        raise AppError("invalid_cursor", "列表游标无效，请重新加载。")
    statement = select(ReviewSession).where(ReviewSession.owner_id == owner_id)
    if before_date is not None and before_id is not None:
        statement = statement.where(
            or_(
                ReviewSession.activity_date < before_date,
                and_(
                    ReviewSession.activity_date == before_date,
                    ReviewSession.id < before_id,
                ),
            )
        )
    rows = list(
        await session.scalars(
            statement.order_by(
                ReviewSession.activity_date.desc(),
                ReviewSession.id.desc(),
            ).limit(limit + 1)
        )
    )
    page = rows[:limit]
    items = []
    for review in page:
        report_count, latest_version = (
            await session.execute(
                select(
                    func.count(ReviewReportVersion.id),
                    func.max(ReviewReportVersion.version),
                ).where(
                    ReviewReportVersion.owner_id == owner_id,
                    ReviewReportVersion.review_session_id == review.id,
                )
            )
        ).one()
        items.append(
            {
                **review_result(review),
                "report_count": report_count,
                "latest_report_version": latest_version,
            }
        )
    return {
        "items": items,
        "next_cursor": (
            {
                "before_date": page[-1].activity_date,
                "before_id": page[-1].id,
            }
            if len(rows) > limit
            else None
        ),
    }


async def review_report_detail(
    session: AsyncSession,
    owner_id: UUID,
    review_id: UUID,
    version: int,
) -> dict:
    await owned_review(session, owner_id, review_id)
    report = await session.scalar(
        select(ReviewReportVersion).where(
            ReviewReportVersion.owner_id == owner_id,
            ReviewReportVersion.review_session_id == review_id,
            ReviewReportVersion.version == version,
        )
    )
    if report is None:
        raise AppError(
            "review_report_not_found",
            "未找到此复盘报告版本。",
            status=404,
        )
    annotations = list(
        await session.scalars(
            select(ReviewAnnotation)
            .where(
                ReviewAnnotation.owner_id == owner_id,
                ReviewAnnotation.report_version_id == report.id,
                ReviewAnnotation.deleted_at.is_(None),
            )
            .order_by(ReviewAnnotation.created_at, ReviewAnnotation.id)
        )
    )
    proposals = list(
        await session.scalars(
            select(MemoryProposal)
            .where(
                MemoryProposal.owner_id == owner_id,
                MemoryProposal.report_version_id == report.id,
            )
            .order_by(MemoryProposal.position, MemoryProposal.id)
        )
    )
    snapshot = await session.get(ReviewInputSnapshot, report.input_snapshot_id)
    return {
        "report": report_result(report),
        "annotations": [annotation_result(item) for item in annotations],
        "memory_proposals": [memory_proposal_result(item) for item in proposals],
        "snapshot": (
            {
                "activity_date": snapshot.activity_date,
                "time_zone": snapshot.time_zone,
                "session_input_revision": snapshot.session_input_revision,
                "facts_sha256": snapshot.facts_sha256,
                "created_at": snapshot.created_at,
            }
            if snapshot is not None
            else None
        ),
    }


async def owned_report(
    session: AsyncSession,
    owner_id: UUID,
    report_id: UUID,
) -> ReviewReportVersion:
    report = await session.scalar(
        select(ReviewReportVersion).where(
            ReviewReportVersion.id == report_id,
            ReviewReportVersion.owner_id == owner_id,
        )
    )
    if report is None:
        raise AppError(
            "review_report_not_found",
            "未找到此复盘报告版本。",
            status=404,
        )
    return report


async def owned_annotation(
    session: AsyncSession,
    owner_id: UUID,
    annotation_id: UUID,
    *,
    lock: bool = False,
) -> ReviewAnnotation:
    statement = select(ReviewAnnotation).where(
        ReviewAnnotation.id == annotation_id,
        ReviewAnnotation.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    annotation = await session.scalar(statement)
    if annotation is None or annotation.deleted_at is not None:
        raise AppError(
            "review_annotation_not_found",
            "未找到此报告批注。",
            status=404,
        )
    return annotation


def annotation_version_conflict(annotation: ReviewAnnotation) -> AppError:
    return AppError(
        "review_annotation_version_conflict",
        "此批注已在其他页面或设备更新，请刷新后比较再保存。",
        status=409,
        details={"current_version": annotation.version},
    )


async def create_annotation(
    session: AsyncSession,
    owner_id: UUID,
    report_id: UUID,
    data: ReviewAnnotationCreateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"review_annotation.create:{report_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    await owned_report(session, owner_id, report_id)
    annotation = ReviewAnnotation(
        owner_id=owner_id,
        report_version_id=report_id,
        content=data.content,
        deleted_at=None,
        version=1,
        created_at=current_time,
        updated_at=current_time,
    )
    session.add(annotation)
    await session.flush()
    emit_change(
        session,
        owner_id,
        "review_annotation",
        annotation.id,
        "created",
        annotation.version,
    )
    result = {
        "operation_id": data.operation_id,
        "annotation": annotation_result(annotation),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result


async def update_annotation(
    session: AsyncSession,
    owner_id: UUID,
    annotation_id: UUID,
    data: ReviewAnnotationUpdateInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"review_annotation.update:{annotation_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    annotation = await owned_annotation(
        session,
        owner_id,
        annotation_id,
        lock=True,
    )
    if annotation.version != data.expected_version:
        raise annotation_version_conflict(annotation)
    if annotation.content != data.content:
        annotation.content = data.content
        annotation.version += 1
        annotation.updated_at = current_time
        emit_change(
            session,
            owner_id,
            "review_annotation",
            annotation.id,
            "updated",
            annotation.version,
        )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "annotation": annotation_result(annotation),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result


async def delete_annotation(
    session: AsyncSession,
    owner_id: UUID,
    annotation_id: UUID,
    data: ReviewAnnotationDeleteInput,
    *,
    current_time: datetime,
) -> dict:
    scope = f"review_annotation.delete:{annotation_id}"
    replay = await replay_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
    )
    if replay is not None:
        return replay
    annotation = await owned_annotation(
        session,
        owner_id,
        annotation_id,
        lock=True,
    )
    if annotation.version != data.expected_version:
        raise annotation_version_conflict(annotation)
    annotation.deleted_at = current_time
    annotation.version += 1
    annotation.updated_at = current_time
    emit_change(
        session,
        owner_id,
        "review_annotation",
        annotation.id,
        "deleted",
        annotation.version,
    )
    await session.flush()
    result = {
        "operation_id": data.operation_id,
        "annotation": annotation_result(annotation),
    }
    save_operation(
        session,
        owner_id,
        data.operation_id,
        scope,
        data,
        result,
    )
    return result
