from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.job_service import (
    cancel_active_ai_jobs_for_owner,
    clear_temporary_media_for_account_deletion,
)
from itime.auth.mail import invalidate_account_mail
from itime.calendar.subscription_service import (
    invalidate_subscription_refreshes_for_account_deletion,
)
from itime.data_export.service import expire_exports_for_account_deletion
from itime.execution.service import (
    abandon_open_focus_for_account_deletion,
)


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
        ai_jobs=await cancel_active_ai_jobs_for_owner(
            session,
            owner_id,
        ),
        exports=await expire_exports_for_account_deletion(
            session,
            owner_id,
            requested_at,
        ),
        subscription_refreshes=(
            await invalidate_subscription_refreshes_for_account_deletion(
                session,
                owner_id,
                requested_at,
            )
        ),
        focus_sessions=await abandon_open_focus_for_account_deletion(
            session,
            owner_id,
            requested_at,
        ),
        emails=await invalidate_account_mail(
            session,
            owner_id,
            requested_at,
        ),
        temporary_media=await clear_temporary_media_for_account_deletion(
            session,
            owner_id,
            requested_at,
        ),
    )
