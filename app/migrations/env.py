import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import db


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=db.Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    engine = create_async_engine(db.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


asyncio.run(main())
