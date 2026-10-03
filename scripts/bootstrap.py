"""创建仅本地可读的开发配置；已有文件绝不覆盖。不输出密钥。"""

import base64
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env.local"
password = secrets.token_urlsafe(32)
values = {
    "POSTGRES_PASSWORD": password,
    "POSTGRES_PORT": "55432",
    "SMTP_PORT": "51025",
    "MAILPIT_PORT": "58025",
    "ITIME_ENVIRONMENT": "development",
    "ITIME_DATABASE_URL": f"postgresql+psycopg://itime:{password}@127.0.0.1:55432/itime",
    "ITIME_PUBLIC_ORIGIN": "http://localhost:5173",
    "ITIME_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "ITIME_AI_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "ITIME_CALENDAR_SUBSCRIPTION_ENCRYPTION_KEY": base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode(),
    "ITIME_EXPORT_ENCRYPTION_KEY": base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode(),
}
try:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("已存在 .env.local，保留原配置。")
else:
    with os.fdopen(fd, "w") as stream:
        stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    print("已创建 .env.local（权限 0600），请勿提交或复制到文档。")
