import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

from itime.config import Settings
from itime.database import Database
from performance.contracts import AccountCredential, SeedManifest, SeedProfile
from performance.seed_core import seed_standard
from performance.seed_verify import verify_dataset

LOOPBACK_DATABASE_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres"})


class SeedVerificationError(RuntimeError):
    """性能数据写入后不满足清单或业务不变量。"""


def require_safe_seed_settings(settings: Settings) -> None:
    url = make_url(settings.database_url.get_secret_value())
    database_name = url.database or ""
    if settings.environment != "test":
        raise ValueError("性能数据只允许写入 test 环境")
    if url.host not in LOOPBACK_DATABASE_HOSTS:
        raise ValueError("性能数据只允许写入本地或专用 Compose 数据库")
    if not database_name.startswith(("itime_perf_", "itime_test_")):
        raise ValueError("性能数据库名必须以 itime_perf_ 或 itime_test_ 开头")


def require_safe_upstream_origin(origin: str) -> None:
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname
        not in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("性能上游必须为无凭据、无路径的本机 HTTP origin")


def write_private_credentials(
    path: Path,
    credentials: tuple[AccountCredential, ...],
) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            json.dump(
                [
                    {
                        "email": item.email,
                        "password": item.password,
                        "dataset": item.dataset,
                    }
                    for item in credentials
                ],
                output,
                ensure_ascii=False,
                indent=2,
            )
            output.write("\n")
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


async def seed_dataset(
    settings: Settings,
    profile: SeedProfile,
    *,
    seed: int,
    at: datetime,
    credentials_path: Path,
    upstream_origin: str = "http://host.docker.internal:18081",
) -> SeedManifest:
    require_safe_seed_settings(settings)
    require_safe_upstream_origin(upstream_origin)
    database = Database(settings)
    try:
        if profile.name == "standard":
            manifest = await seed_standard(
                database,
                settings,
                profile,
                seed=seed,
                at=at,
                upstream_origin=upstream_origin,
            )
        elif profile.name == "history":
            from performance.seed_history import seed_history_accounts

            manifest = await seed_history_accounts(
                database,
                settings,
                profile,
                seed=seed,
                at=at,
                upstream_origin=upstream_origin,
            )
        else:
            raise ValueError(f"不支持的数据档位：{profile.name}")
        verification = await verify_dataset(settings, manifest, database=database)
        if not verification.ok:
            detail = "; ".join(verification.violations)
            raise SeedVerificationError(f"性能数据校验失败：{detail}")
        write_private_credentials(credentials_path, manifest.credentials)
        return manifest
    finally:
        await database.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 iTime 隔离性能测试数据")
    parser.add_argument("--profile", choices=("standard", "history"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument(
        "--upstream-origin",
        default="http://host.docker.internal:18081",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = (
        SeedProfile.standard()
        if args.profile == "standard"
        else SeedProfile.history()
    )
    manifest = asyncio.run(
        seed_dataset(
            Settings(),
            profile,
            seed=args.seed,
            at=args.at,
            credentials_path=args.credentials,
            upstream_origin=args.upstream_origin,
        )
    )
    print(json.dumps(manifest.to_public_dict(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
