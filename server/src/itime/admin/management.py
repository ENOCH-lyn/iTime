from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import and_, or_, select, update

from itime.admin.models import RegistrationInvitation, RegistrationPolicy
from itime.admin.security import authorized_admin
from itime.auth.models import DeviceSession, EmailToken, User
from itime.auth.security import digest, new_token, now
from itime.auth.session import audit
from itime.errors import AppError

router = APIRouter(prefix="/api/v1/admin", tags=["后台用户管理"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class RegistrationPolicyInput(Input):
    mode: Literal["open", "invite"]
    expected_version: int = Field(ge=1)


class InvitationInput(Input):
    email: EmailStr
    expires_in_hours: int = Field(ge=1, le=24 * 30)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class UserStatusInput(Input):
    status: Literal["active", "suspended"]
    expected_version: int = Field(ge=1)


class UserAIAccessInput(Input):
    ai_access: Literal["allowed", "frozen"]
    expected_version: int = Field(ge=1)


def validate_cursor(before_time: datetime | None, before_id: UUID | None) -> None:
    if (before_time is None) != (before_id is None) or (
        before_time is not None and before_time.tzinfo is None
    ):
        raise AppError("invalid_cursor", "列表游标无效，请重新加载。")


def registration_policy_result(policy: RegistrationPolicy) -> dict:
    return {
        "mode": policy.mode,
        "version": policy.version,
        "updated_at": policy.updated_at,
    }


def invitation_state(invitation: RegistrationInvitation, current: datetime) -> str:
    if invitation.consumed_at is not None:
        return "consumed"
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.expires_at <= current:
        return "expired"
    return "pending"


def invitation_result(invitation: RegistrationInvitation, current: datetime) -> dict:
    return {
        "id": invitation.id,
        "email": invitation.email,
        "status": invitation_state(invitation, current),
        "created_at": invitation.created_at,
        "expires_at": invitation.expires_at,
        "consumed_at": invitation.consumed_at,
        "revoked_at": invitation.revoked_at,
    }


def user_result(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "email_verified": user.email_verified_at is not None,
        "status": user.status,
        "ai_access": user.ai_access,
        "is_admin": user.is_admin,
        "version": user.management_version,
        "created_at": user.created_at,
    }


def version_conflict(kind: str, version: int) -> AppError:
    return AppError(
        f"{kind}_version_conflict",
        "此内容已被其他管理操作更新，请刷新后重试。",
        status=409,
        details={"current_version": version},
    )


@router.get("/registration-policy")
async def get_registration_policy(request: Request) -> dict:
    async with authorized_admin(request) as identity:
        policy = await identity.session.get(RegistrationPolicy, 1)
        if policy is None:
            raise AppError(
                "registration_policy_unavailable",
                "注册策略暂时不可用，请检查数据库迁移后重试。",
                status=503,
                retryable=True,
            )
        return registration_policy_result(policy)


@router.put("/registration-policy")
async def update_registration_policy(
    data: RegistrationPolicyInput, request: Request
) -> dict:
    async with authorized_admin(request, write=True) as identity:
        policy = await identity.session.scalar(
            select(RegistrationPolicy)
            .where(RegistrationPolicy.id == 1)
            .with_for_update()
        )
        if policy is None:
            raise AppError(
                "registration_policy_unavailable",
                "注册策略暂时不可用，请检查数据库迁移后重试。",
                status=503,
                retryable=True,
            )
        if policy.version != data.expected_version:
            raise version_conflict("registration_policy", policy.version)
        if policy.mode != data.mode:
            policy.mode = data.mode
            policy.version += 1
            policy.updated_at = now()
            policy.updated_by_user_id = identity.user.id
            audit(
                request,
                identity.session,
                identity.user,
                f"registration_{data.mode}_enabled",
            )
        return registration_policy_result(policy)


@router.post("/invitations", status_code=201)
async def create_invitation(data: InvitationInput, request: Request) -> dict:
    raw = new_token()
    current = now()
    async with authorized_admin(request, write=True) as identity:
        existing = await identity.session.scalar(
            select(User.id).where(User.email == data.email)
        )
        if existing is not None:
            raise AppError(
                "email_already_registered",
                "此邮箱已经注册，无需创建邀请。",
                status=409,
            )
        invitation = RegistrationInvitation(
            email=data.email,
            token_hash=digest(raw),
            issued_by_user_id=identity.user.id,
            expires_at=current + timedelta(hours=data.expires_in_hours),
        )
        identity.session.add(invitation)
        await identity.session.flush()
        audit(
            request,
            identity.session,
            identity.user,
            "registration_invitation_created",
            invitation.id,
        )
        result = invitation_result(invitation, current)
        result["invite_url"] = (
            f"{request.app.state.settings.public_origin}/register#invite={raw}"
        )
        return result


@router.get("/invitations")
async def list_invitations(
    request: Request,
    status: Literal["all", "pending", "consumed", "expired", "revoked"] = "all",
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    validate_cursor(before_time, before_id)
    current = now()
    async with authorized_admin(request) as identity:
        statement = select(RegistrationInvitation)
        if status == "pending":
            statement = statement.where(
                RegistrationInvitation.consumed_at.is_(None),
                RegistrationInvitation.revoked_at.is_(None),
                RegistrationInvitation.expires_at > current,
            )
        elif status == "consumed":
            statement = statement.where(
                RegistrationInvitation.consumed_at.is_not(None)
            )
        elif status == "expired":
            statement = statement.where(
                RegistrationInvitation.consumed_at.is_(None),
                RegistrationInvitation.revoked_at.is_(None),
                RegistrationInvitation.expires_at <= current,
            )
        elif status == "revoked":
            statement = statement.where(
                RegistrationInvitation.consumed_at.is_(None),
                RegistrationInvitation.revoked_at.is_not(None),
            )
        if before_time is not None:
            statement = statement.where(
                or_(
                    RegistrationInvitation.created_at < before_time,
                    and_(
                        RegistrationInvitation.created_at == before_time,
                        RegistrationInvitation.id < before_id,
                    ),
                )
            )
        invitations = list(
            await identity.session.scalars(
                statement.order_by(
                    RegistrationInvitation.created_at.desc(),
                    RegistrationInvitation.id.desc(),
                ).limit(limit + 1)
            )
        )
        page = invitations[:limit]
        return {
            "items": [invitation_result(item, current) for item in page],
            "next_cursor": (
                {"before_time": page[-1].created_at, "before_id": page[-1].id}
                if len(invitations) > limit
                else None
            ),
        }


@router.delete("/invitations/{invitation_id}")
async def revoke_invitation(invitation_id: UUID, request: Request) -> dict:
    current = now()
    async with authorized_admin(request, write=True) as identity:
        invitation = await identity.session.scalar(
            select(RegistrationInvitation)
            .where(RegistrationInvitation.id == invitation_id)
            .with_for_update()
        )
        if invitation is None:
            raise AppError("invitation_not_found", "未找到此邀请。", status=404)
        if invitation.consumed_at is not None:
            raise AppError(
                "invitation_already_consumed",
                "此邀请已经用于注册，不能撤销。",
                status=409,
            )
        if invitation.revoked_at is None:
            invitation.revoked_at = current
            audit(
                request,
                identity.session,
                identity.user,
                "registration_invitation_revoked",
                invitation.id,
            )
        return invitation_result(invitation, current)


@router.get("/users")
async def list_users(
    request: Request,
    search: Annotated[str | None, Query(max_length=254)] = None,
    status: Literal["all", "active", "suspended"] = "all",
    ai_access: Literal["all", "allowed", "frozen"] = "all",
    role: Literal["all", "user", "admin"] = "all",
    before_time: datetime | None = None,
    before_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    validate_cursor(before_time, before_id)
    async with authorized_admin(request) as identity:
        statement = select(User)
        if search and (normalized := search.strip().lower()):
            statement = statement.where(User.email.contains(normalized, autoescape=True))
        if status != "all":
            statement = statement.where(User.status == status)
        if ai_access != "all":
            statement = statement.where(User.ai_access == ai_access)
        if role != "all":
            statement = statement.where(User.is_admin.is_(role == "admin"))
        if before_time is not None:
            statement = statement.where(
                or_(
                    User.created_at < before_time,
                    and_(User.created_at == before_time, User.id < before_id),
                )
            )
        users = list(
            await identity.session.scalars(
                statement.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)
            )
        )
        page = users[:limit]
        return {
            "items": [user_result(user) for user in page],
            "next_cursor": (
                {"before_time": page[-1].created_at, "before_id": page[-1].id}
                if len(users) > limit
                else None
            ),
        }


async def locked_managed_user(identity, user_id: UUID) -> User:
    user = await identity.session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None:
        raise AppError("user_not_found", "未找到此用户。", status=404)
    if user.is_admin:
        raise AppError(
            "admin_account_protected",
            "管理员账号需通过服务器交接流程处理，不能在用户列表中修改。",
            status=409,
        )
    return user


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: UUID, data: UserStatusInput, request: Request
) -> dict:
    async with authorized_admin(request, write=True) as identity:
        user = await locked_managed_user(identity, user_id)
        if user.management_version != data.expected_version:
            raise version_conflict("user", user.management_version)
        if user.status != data.status:
            user.status = data.status
            user.management_version += 1
            if data.status == "suspended":
                user.auth_epoch += 1
                current = now()
                await identity.session.execute(
                    update(DeviceSession)
                    .where(
                        DeviceSession.user_id == user.id,
                        DeviceSession.revoked_at.is_(None),
                    )
                    .values(revoked_at=current)
                )
                await identity.session.execute(
                    update(EmailToken)
                    .where(
                        EmailToken.user_id == user.id,
                        EmailToken.used_at.is_(None),
                    )
                    .values(used_at=current)
                )
            audit(
                request,
                identity.session,
                identity.user,
                f"user_{data.status}",
                user.id,
            )
        return user_result(user)


@router.put("/users/{user_id}/ai-access")
async def update_user_ai_access(
    user_id: UUID, data: UserAIAccessInput, request: Request
) -> dict:
    async with authorized_admin(request, write=True) as identity:
        user = await locked_managed_user(identity, user_id)
        if user.management_version != data.expected_version:
            raise version_conflict("user", user.management_version)
        if user.ai_access != data.ai_access:
            user.ai_access = data.ai_access
            user.management_version += 1
            audit(
                request,
                identity.session,
                identity.user,
                f"user_ai_{data.ai_access}",
                user.id,
            )
        return user_result(user)
