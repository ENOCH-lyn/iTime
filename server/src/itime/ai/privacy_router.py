from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from itime.ai.governance_models import UserPrivacySetting
from itime.ai.governance_schemas import PrivacySettingsUpdate
from itime.auth.security import now
from itime.auth.session import audit, authorized
from itime.errors import AppError
from itime.tasks.service import emit_change, replay_operation, save_operation

router = APIRouter(prefix="/api/v1/privacy-settings", tags=["隐私设置"])


def privacy_settings_result(settings: UserPrivacySetting) -> dict:
    return {
        "quality_improvement_enabled": settings.quality_improvement_enabled,
        "version": settings.version,
        "updated_at": settings.updated_at,
    }


async def privacy_settings(
    session: AsyncSession,
    owner_id: UUID,
    *,
    lock: bool = False,
) -> UserPrivacySetting:
    statement = select(UserPrivacySetting).where(
        UserPrivacySetting.owner_id == owner_id
    )
    if lock:
        statement = statement.with_for_update()
    settings = await session.scalar(statement)
    if settings is None:
        raise AppError(
            "privacy_settings_unavailable",
            "隐私设置暂时不可用，请保留请求编号并稍后重试。",
            status=503,
            retryable=True,
        )
    return settings


@router.get("")
async def get_privacy_settings(request: Request) -> dict:
    async with authorized(request) as identity:
        settings = await privacy_settings(identity.session, identity.user.id)
        return {"settings": privacy_settings_result(settings)}


@router.put("")
async def update_privacy_settings(
    data: PrivacySettingsUpdate,
    request: Request,
) -> dict:
    scope = "privacy_settings.update"
    async with authorized(request, write=True) as identity:
        replay = await replay_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
        )
        if replay is not None:
            return replay
        settings = await privacy_settings(
            identity.session,
            identity.user.id,
            lock=True,
        )
        if settings.version != data.expected_version:
            raise AppError(
                "privacy_settings_version_conflict",
                "隐私设置已在其他页面或设备更新，请刷新后比较再保存。",
                status=409,
                details={"current_version": settings.version},
            )
        if (
            settings.quality_improvement_enabled
            != data.quality_improvement_enabled
        ):
            settings.quality_improvement_enabled = (
                data.quality_improvement_enabled
            )
            settings.version += 1
            settings.updated_at = now()
            emit_change(
                identity.session,
                identity.user.id,
                "privacy_settings",
                identity.user.id,
                "updated",
                settings.version,
            )
            audit(
                request,
                identity.session,
                identity.user,
                (
                    "ai_quality_improvement_enabled"
                    if data.quality_improvement_enabled
                    else "ai_quality_improvement_disabled"
                ),
                identity.user.id,
            )
        await identity.session.flush()
        result = {
            "operation_id": data.operation_id,
            "settings": privacy_settings_result(settings),
        }
        save_operation(
            identity.session,
            identity.user.id,
            data.operation_id,
            scope,
            data,
            result,
        )
        return result
