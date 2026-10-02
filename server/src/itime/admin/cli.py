import argparse
import asyncio
import getpass
import sys

from itime.admin.operations import AdminOperationError, create_admin, reset_admin_totp
from itime.auth.security import Security
from itime.config import get_settings
from itime.database import Database


async def execute(args: argparse.Namespace) -> str:
    settings = get_settings()
    database = Database(settings)
    security = Security(settings)
    try:
        if args.command == "create":
            password = (
                sys.stdin.readline().rstrip("\n")
                if args.password_stdin
                else getpass.getpass("管理员密码：")
            )
            if not args.password_stdin:
                confirmation = getpass.getpass("再次输入管理员密码：")
                if password != confirmation:
                    raise AdminOperationError("两次输入的密码不一致。")
            return await create_admin(database, security, settings, args.email, password)
        return await reset_admin_totp(database, settings, args.email)
    finally:
        await database.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="iTime 管理员本机恢复工具")
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="创建新管理员并生成一次性设置链接")
    create.add_argument("--email", required=True)
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取一行密码，仅用于受控自动化",
    )
    reset = commands.add_parser("reset-totp", help="撤销管理员会话并生成新的设置链接")
    reset.add_argument("--email", required=True)
    return result


def main() -> None:
    try:
        link = asyncio.run(execute(parser().parse_args()))
    except AdminOperationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from None
    print("一次性设置链接（请通过安全渠道交给对应管理员）：")
    print(link)


if __name__ == "__main__":
    main()
