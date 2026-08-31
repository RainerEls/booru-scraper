import logging
from datetime import datetime, timezone

from sqlalchemy import (
    UniqueConstraint,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app import config

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

DB_USER = config.DB_USER
DB_PASS = config.DB_PASS
DB_HOST = config.DB_HOST
DB_PORT = config.DB_PORT
DB_NAME = config.DB_NAME

engine = create_async_engine(
    f"postgresql+asyncpg://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
    echo=False,
)


# ------------------------ #
#          Tables          #
# ------------------------ #
class Base(DeclarativeBase):
    pass


class Posts(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("source", "source_post_id"),
        UniqueConstraint("szurubooru_post_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    status: Mapped[str] = mapped_column()
    source: Mapped[str] = mapped_column()
    source_post_id: Mapped[int] = mapped_column()
    szurubooru_post_id: Mapped[int] = mapped_column(nullable=True)
    md5: Mapped[str] = mapped_column()
    image_url: Mapped[str] = mapped_column()


class Tags(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    tag: Mapped[str] = mapped_column()
    type: Mapped[int] = mapped_column()


class Runs(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("uuid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
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


# ------------------------ #
#        Functions         #
# ------------------------ #
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    await engine.dispose()


async def post_exists(source, source_post_id):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        source_post_known = select(Posts).where(
            Posts.source == source, Posts.source_post_id == source_post_id
        )
        result = (await session.execute(source_post_known)).first()

    return bool(result is not None)


async def md5_exists(md5):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        md5_existing = select(Posts).where(Posts.md5 == md5)
        result = (await session.execute(md5_existing)).first()

    return bool(result is not None)


async def claim_post(source, source_post_id, md5, image_url, status):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        claim = (
            pg_insert(Posts)
            .values(
                source=source,
                source_post_id=source_post_id,
                szurubooru_post_id=None,
                md5=md5,
                image_url=image_url,
                status=status,
            )
            .on_conflict_do_nothing(index_elements=["source", "source_post_id"])
            .returning(Posts.id)
        )
        result = await session.execute(claim)
        await session.commit()
        return result.scalar_one_or_none()


async def release_claims(post_ids):
    if not post_ids:
        return
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(
            delete(Posts).where(Posts.id.in_(post_ids), Posts.status == "queued")
        )
        await session.commit()


async def release_stale_claims():
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(delete(Posts).where(Posts.status == "queued"))
        await session.commit()


async def tag_exists(tag_name):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        tag_name_exists = select(Tags).where(Tags.tag == tag_name)
        result = (await session.execute(tag_name_exists)).first()

    return bool(result is not None)


async def store_tag(tag_name, tag_type):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        save_tag = (
            pg_insert(Tags)
            .values(tag=tag_name, type=tag_type)
            .on_conflict_do_nothing(index_elements=["tag"])
        )
        await session.execute(save_tag)
        await session.commit()


async def add_szurubooru_post_id(id, szurubooru_post_id):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        find_id = select(Posts).where(Posts.id == id)
        id_found = (await session.execute(find_id)).scalar_one()
        id_found.szurubooru_post_id = szurubooru_post_id
        await session.commit()


async def update_post_status(source, source_post_id, status):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        find_post = select(Posts).where(
            Posts.source == source, Posts.source_post_id == source_post_id
        )
        post_found = (await session.execute(find_post)).scalar_one()
        post_found.status = status
        await session.commit()


async def get_runs():
    async with AsyncSession(engine, expire_on_commit=False) as session:
        get_entries = select(Runs).order_by(Runs.created_at.desc())
        result = (await session.execute(get_entries)).scalars().all()
    return result


async def create_run(uuid, source, tags, blacklist_tags, rating, post_limit, status):
    async with AsyncSession(engine, expire_on_commit=False) as session:
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
        await session.flush()
        return run.id


async def update_run(id, total, processed, skipped, failed, status):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        find_run = select(Runs).where(Runs.id == id)
        run_found = (await session.execute(find_run)).scalar_one()
        run_found.total = total
        run_found.processed = processed
        run_found.skipped = skipped
        run_found.failed = failed
        run_found.status = status
        await session.commit()
