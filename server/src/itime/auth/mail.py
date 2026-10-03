from datetime import datetime, timedelta
from email.message import EmailMessage
from uuid import UUID

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from itime.auth.models import EmailToken, MailOutbox, User
from itime.auth.security import digest, new_token, now


async def queue_email(request: Request, session: AsyncSession, user: User, purpose: str) -> bool:
    """调用方持有用户锁；令牌与发件记录一起提交，重发冷却期间不重复排队。"""
    settings = request.app.state.settings
    current = now()
    latest = await session.scalar(
        select(EmailToken.created_at)
        .where(EmailToken.user_id == user.id, EmailToken.purpose == purpose)
        .order_by(EmailToken.created_at.desc())
        .limit(1)
    )
    if latest and latest > current - timedelta(seconds=settings.mail_cooldown_seconds):
        return False
    await session.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=current)
    )
    minutes = (
        settings.verification_minutes if purpose == "verify" else settings.password_reset_minutes
    )
    raw = new_token()
    token = EmailToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=digest(raw),
        created_at=current,
        expires_at=current + timedelta(minutes=minutes),
    )
    session.add(token)
    await session.flush()
    page = "verify-email" if purpose == "verify" else "reset-password"
    label = "验证邮箱" if purpose == "verify" else "重设密码"
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = user.email
    message["Subject"] = f"iTime · {label}"
    message["Message-ID"] = f"<{token.id}@itime.local>"
    message.set_content(
        f"请打开以下链接{label}，链接 {minutes} 分钟内有效且只能使用一次：\n\n"
        f"{settings.public_origin}/{page}#token={raw}\n\n"
        + (
            "验证时需要输入注册密码。如果你没有注册，请忽略此邮件。\n"
            if purpose == "verify"
            else "重设密码后所有已登录设备将退出。如果不是你发起的，请忽略此邮件。\n"
        )
        + "iTime 不会通过邮件向你索要密码。"
    )
    session.add(
        MailOutbox(
            token_id=token.id,
            encrypted_message=request.app.state.security.cipher.encrypt(message.as_bytes()),
            available_at=current,
        )
    )
    return True


async def invalidate_account_mail(
    session: AsyncSession,
    owner_id: UUID,
    at: datetime,
) -> int:
    outbox_rows = list(
        await session.scalars(
            select(MailOutbox)
            .join(EmailToken, EmailToken.id == MailOutbox.token_id)
            .where(
                EmailToken.user_id == owner_id,
                MailOutbox.finished_at.is_(None),
                MailOutbox.encrypted_message.is_not(None),
            )
            .order_by(MailOutbox.available_at, MailOutbox.id)
            .with_for_update(of=MailOutbox)
        )
    )
    await session.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == owner_id,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=at)
    )
    for mail in outbox_rows:
        mail.encrypted_message = None
        mail.lease_id = None
        mail.lease_until = None
        mail.finished_at = at
        mail.error_code = "account_deletion"
    return len(outbox_rows)
