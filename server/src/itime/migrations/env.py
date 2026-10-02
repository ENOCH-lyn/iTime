import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from itime.admin import models as admin_models  # noqa: F401
from itime.auth import models as auth_models  # noqa: F401
from itime.config import get_settings
from itime.database import Base, Database
from itime.tasks import models as task_models  # noqa: F401

target_metadata = Base.metadata


def apply_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    database = Database(get_settings())
    try:
        async with database.engine.connect() as connection:
            # 防止两个发布进程同时迁移；短等待，不能无限挂住部署。
            await connection.exec_driver_sql("SET lock_timeout = '5s'")
            await connection.exec_driver_sql("SELECT pg_advisory_lock(84696301)")
            await connection.commit()
            try:
                await connection.run_sync(apply_migrations)
            finally:
                await connection.exec_driver_sql("SELECT pg_advisory_unlock(84696301)")
                await connection.commit()
    finally:
        await database.close()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql", target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
