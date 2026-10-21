"""Create the local administrator account once.

The generated credentials are written to ``server/.local/admin-bootstrap.json``
which is ignored by git. The password is not printed to stdout.
"""

import asyncio
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from itime.admin.operations import AdminOperationError, create_admin
from itime.auth.security import Security
from itime.config import get_settings
from itime.database import Database

EMAIL = "admin@itime.local"
LOCAL_PATH = Path(__file__).resolve().parents[1] / ".local" / "admin-bootstrap.json"


async def main() -> None:
    if LOCAL_PATH.exists():
        print(f"管理员引导信息已存在：{LOCAL_PATH}")
        return
    settings = get_settings()
    database = Database(settings)
    security = Security(settings)
    password = secrets.token_urlsafe(18)
    try:
        setup_link = await create_admin(
            database,
            security,
            settings,
            EMAIL,
            password,
        )
    except AdminOperationError as exc:
        print(f"无法创建管理员：{exc}")
        return
    finally:
        await database.close()
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_PATH.write_text(
        json.dumps(
            {
                "email": EMAIL,
                "password": password,
                "setup_link": setup_link,
                "created_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"管理员已创建：{EMAIL}")
    print(f"凭据与 TOTP 设置链接：{LOCAL_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
