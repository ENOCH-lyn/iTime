from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.job_service import (
    cancel_active_ai_jobs_for_owner,
    clear_temporary_media_for_account_deletion,
    purge_voice_transcriptions_for_account_deletion,
)
from itime.ai.models import TemporaryMedia
from itime.auth.mail import invalidate_account_mail
from itime.calendar.subscription_service import (
    invalidate_subscription_refreshes_for_account_deletion,
)
from itime.data_export.service import expire_exports_for_account_deletion
from itime.execution.service import (
    abandon_open_focus_for_account_deletion,
)
from itime.public_links.service import (
    invalidate_public_link_fetches_for_account_deletion,
)


@dataclass(frozen=True)
class QuiesceSummary:
    ai_jobs: int
    exports: int
    subscription_refreshes: int
    focus_sessions: int
    emails: int
    temporary_media: int
    public_links: int


async def quiesce_account(
    session: AsyncSession,
    owner_id: UUID,
    *,
    requested_at: datetime,
) -> QuiesceSummary:
    temporary_media = await session.scalar(
        select(func.count())
        .select_from(TemporaryMedia)
        .where(
            TemporaryMedia.owner_id == owner_id,
            TemporaryMedia.encrypted_content.is_not(None),
        )
    )
    ai_jobs = await cancel_active_ai_jobs_for_owner(
        session,
        owner_id,
    )
    await purge_voice_transcriptions_for_account_deletion(
        session,
        owner_id,
        requested_at,
    )
    await clear_temporary_media_for_account_deletion(
        session,
        owner_id,
        requested_at,
    )
    return QuiesceSummary(
        ai_jobs=ai_jobs,
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
        temporary_media=int(temporary_media or 0),
        public_links=await invalidate_public_link_fetches_for_account_deletion(
            session,
            owner_id,
            requested_at,
        ),
    )
