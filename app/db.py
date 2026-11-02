import asyncio
import logging
from datetime import UTC, datetime

import alembic.command
import alembic.config
from sqlalchemy import JSON, UniqueConstraint, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app import config

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

DATABASE_URL = (
    f"postgresql+asyncpg://{config.DB_USER}:{config.DB_PASS}@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
)
engine = create_async_engine(DATABASE_URL)
Session = async_sessionmaker(engine, expire_on_commit=False)

CHECK_BUSY = ("running", "repulling")


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Posts(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("source", "source_post_id"),
        UniqueConstraint("szurubooru_post_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    status: Mapped[str] = mapped_column()
    source: Mapped[str] = mapped_column()
    source_post_id: Mapped[int] = mapped_column()
    szurubooru_post_id: Mapped[int] = mapped_column(nullable=True)
    md5: Mapped[str] = mapped_column(index=True)
    image_url: Mapped[str] = mapped_column()


class Tags(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    tag: Mapped[str] = mapped_column()


class Runs(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("uuid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    source: Mapped[str] = mapped_column()
    tags: Mapped[str] = mapped_column(nullable=True)
    blacklist_tags: Mapped[str] = mapped_column(nullable=True)
    rating: Mapped[str] = mapped_column(nullable=True)
    post_limit: Mapped[int] = mapped_column(nullable=True)
    total: Mapped[int] = mapped_column(nullable=True)
    processed: Mapped[int] = mapped_column(nullable=True)
    skipped: Mapped[int] = mapped_column(nullable=True)
    failed: Mapped[int] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column()
    finished_at: Mapped[datetime] = mapped_column(nullable=True)


class IntegrityChecks(Base):
    __tablename__ = "integrity_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    status: Mapped[str] = mapped_column()
    report: Mapped[dict] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(nullable=True)


async def migrate():
    await asyncio.to_thread(alembic.command.upgrade, alembic.config.Config("alembic.ini"), "head")


async def close_db():
    await engine.dispose()


async def has_row(table, *conditions):
    async with Session() as session:
        return (await session.execute(select(table.id).where(*conditions).limit(1))).first() is not None


async def write(statement):
    async with Session() as session:
        await session.execute(statement)
        await session.commit()


async def post_exists(source, source_post_id):
    return await has_row(Posts, Posts.source == source, Posts.source_post_id == source_post_id)


async def md5_exists(md5):
    return await has_row(Posts, Posts.md5 == md5)


async def tag_exists(tag_name):
    return await has_row(Tags, Tags.tag == tag_name)


async def claim_post(source, source_post_id, md5, image_url, status):
    claim = (
        pg_insert(Posts)
        .values(source=source, source_post_id=source_post_id, md5=md5, image_url=image_url, status=status)
        .on_conflict_do_nothing(index_elements=["source", "source_post_id"])
        .returning(Posts.id)
    )
    async with Session() as session:
        result = await session.execute(claim)
        await session.commit()
        return result.scalar_one_or_none()


async def release_claims(post_ids):
    if post_ids:
        await write(delete(Posts).where(Posts.id.in_(post_ids), Posts.status == "queued"))


async def release_stale_claims():
    await write(delete(Posts).where(Posts.status.in_(("queued", "failed"))))


async def fail_interrupted_runs():
    await write(update(Runs).where(Runs.status == "running").values(status="error"))
    await write(
        update(IntegrityChecks)
        .where(IntegrityChecks.status.in_(CHECK_BUSY))
        .values(status="error", error="Interrupted by a restart")
    )


async def count_tags():
    async with Session() as session:
        return await session.scalar(select(func.count()).select_from(Tags))


async def store_tag(tag_name):
    await write(pg_insert(Tags).values(tag=tag_name).on_conflict_do_nothing(index_elements=["tag"]))


async def finish_post(id, status, szurubooru_post_id=None):
    await write(update(Posts).where(Posts.id == id).values(status=status, szurubooru_post_id=szurubooru_post_id))


async def get_uploaded_posts():
    async with Session() as session:
        return (await session.scalars(select(Posts).where(Posts.szurubooru_post_id.is_not(None)))).all()


async def get_posts(ids):
    async with Session() as session:
        return (await session.scalars(select(Posts).where(Posts.id.in_(ids)))).all()


async def get_checks():
    async with Session() as session:
        latest = select(IntegrityChecks).order_by(IntegrityChecks.created_at.desc()).limit(20)
        return (await session.scalars(latest)).all()


async def create_check():
    async with Session() as session:
        check = IntegrityChecks(status="running")
        session.add(check)
        await session.commit()
        return check.id


async def mark_check_repulling(id):
    await write(update(IntegrityChecks).where(IntegrityChecks.id == id).values(status="repulling"))


async def finish_check(id, status, report=None, error=None):
    await write(
        update(IntegrityChecks).where(IntegrityChecks.id == id).values(status=status, report=report, error=error)
    )


async def get_runs():
    async with Session() as session:
        return (await session.scalars(select(Runs).order_by(Runs.created_at.desc()).limit(100))).all()


async def create_run(uuid, source, tags, blacklist_tags, rating, post_limit, status):
    async with Session() as session:
        run = Runs(
            uuid=uuid,
            source=source,
            tags=tags,
            blacklist_tags=blacklist_tags,
            rating=rating,
            post_limit=post_limit,
            status=status,
        )
        session.add(run)
        await session.commit()
        return run.id


async def update_run(id, total, processed, skipped, failed, status):
    await write(
        update(Runs)
        .where(Runs.id == id)
        .values(total=total, processed=processed, skipped=skipped, failed=failed, status=status, finished_at=utcnow())
    )
