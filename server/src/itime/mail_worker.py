"""独立邮件进程：事务领取、事务外发送、租约恢复；不会阻塞账号 HTTP 请求。"""

import argparse
import asyncio
import logging
from datetime import timedelta
from email import message_from_bytes
from email.policy import SMTP
from uuid import uuid4

import aiosmtplib
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, or_, select

from itime.auth.models import AuthRateBucket, EmailToken, MailOutbox
from itime.auth.security import now
from itime.config import Settings, get_settings
from itime.database import Database
from itime.observability import configure_logging

logger = logging.getLogger("itime.mail")


async def deliver_one(database: Database, settings: Settings) -> bool:
    current = now()
    async with database.transaction() as session:
        row = (
            await session.execute(
                select(MailOutbox, EmailToken)
                .join(EmailToken)
                .where(
                    MailOutbox.finished_at.is_(None),
                    MailOutbox.available_at <= current,
                    or_(MailOutbox.lease_until.is_(None), MailOutbox.lease_until <= current),
                )
                .order_by(MailOutbox.available_at, MailOutbox.id)
                .limit(1)
                .with_for_update(skip_locked=True, of=MailOutbox)
            )
        ).first()
        if row is None:
            return False
        mail, token = row
        if token.expires_at <= current or token.used_at is not None or mail.attempts >= 6:
            mail.finished_at = current
            mail.encrypted_message = None
            mail.error_code = "expired_or_replaced" if mail.attempts < 6 else "attempts_exhausted"
            return True
        lease_id = uuid4()
        mail.lease_id = lease_id
        mail.lease_until = current + timedelta(seconds=60)
        mail.attempts += 1
        mail_id, ciphertext = mail.id, mail.encrypted_message

    error_code = None
    try:
        plaintext = Fernet(settings.encryption_key.get_secret_value()).decrypt(ciphertext)
        message = message_from_bytes(plaintext, policy=SMTP)
        async with asyncio.timeout(settings.smtp_timeout_seconds):
            await aiosmtplib.send(
                message,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                start_tls=settings.smtp_starttls,
                username=settings.smtp_username,
                password=(
                    settings.smtp_password.get_secret_value() if settings.smtp_password else None
                ),
                timeout=settings.smtp_timeout_seconds,
            )
    except InvalidToken:
        # 配置暂时错误时保留密文，允许恢复原密钥后重试；仍受次数和令牌期限限制。
        error_code = "mail_key_unavailable"
    except (aiosmtplib.SMTPException, TimeoutError, OSError) as exc:
        error_code = "smtp_timeout" if isinstance(exc, TimeoutError) else "smtp_unavailable"
    # 取消/进程退出保留租约；另一个进程在 60 秒后重新领取。
    # SMTP 不支持事务提交：发送后进程崩溃可能重发，但 Message-ID/令牌保持相同。
    async with database.transaction() as session:
        mail = await session.scalar(
            select(MailOutbox)
            .where(MailOutbox.id == mail_id, MailOutbox.lease_id == lease_id)
            .with_for_update()
        )
        if mail is None:
            return True
        mail.lease_until = None
        mail.lease_id = None
        mail.error_code = error_code
        if error_code is None or mail.attempts >= 6:
            mail.finished_at = now()
            mail.encrypted_message = None
        else:
            mail.available_at = now() + timedelta(seconds=min(300, 5 * 2 ** (mail.attempts - 1)))
    logger.info("mail_processed", extra={"status": error_code or "sent"})
    return True


async def run(*, once: bool = False) -> None:
    configure_logging()
    settings = get_settings()
    database = Database(settings)
    last_cleanup = now() - timedelta(hours=1)
    try:
        while True:
            try:
                found = await deliver_one(database, settings)
                if now() - last_cleanup > timedelta(minutes=10):
                    async with database.transaction() as session:
                        await session.execute(
                            delete(AuthRateBucket).where(AuthRateBucket.expires_at < now())
                        )
                        # 短期验证令牌及已清空的邮件只保留 24 小时，用于重放判定/排查。
                        await session.execute(
                            delete(EmailToken).where(
                                EmailToken.expires_at < now() - timedelta(hours=24)
                            )
                        )
                    last_cleanup = now()
            except Exception as exc:
                # 不记录 SMTP 异常正文、收件人、链接或邮件内容。
                logger.warning("mail_worker_error", extra={"error_type": type(exc).__name__})
                if once:
                    raise RuntimeError("邮件工作进程失败，请检查数据库与邮件配置。") from None
                found = False
            if once:
                break
            if not found:
                await asyncio.sleep(1)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 iTime 邮件工作进程")
    parser.add_argument("--once", action="store_true", help="处理最多一条后退出")
    args = parser.parse_args()
    asyncio.run(run(once=args.once))


if __name__ == "__main__":
    main()
