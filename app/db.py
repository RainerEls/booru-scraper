import logging
from datetime import datetime

from sqlalchemy import (
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app import config

logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

DB_USER = config.DB_USER
DB_PASS = config.DB_PASS
DB_HOST = config.DB_HOST
DB_PORT = config.DB_PORT
DB_NAME = config.DB_NAME

engine = create_engine(f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}", echo=False)

#------------------------#
#         Tables         #
#------------------------#
class Base(DeclarativeBase):
    pass

class Posts(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint('source', 'source_post_id'),
        UniqueConstraint('szurubooru_post_id'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    status: Mapped[str] = mapped_column()
    source: Mapped[str] = mapped_column()
    source_post_id: Mapped[int] = mapped_column()
    szurubooru_post_id: Mapped[int] = mapped_column(nullable=True)
    md5: Mapped[str] = mapped_column()
    image_url: Mapped[str] = mapped_column()

class Tags(Base):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint('tag'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    tag: Mapped[str] = mapped_column()
    type: Mapped[int] = mapped_column()

class Runs(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint('uuid'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    source: Mapped[str] = mapped_column()
    tags: Mapped[str] = mapped_column(nullable=True)
    blacklist_tags: Mapped[str] = mapped_column(nullable=True)
    rating: Mapped[str] = mapped_column(nullable=True)
    total: Mapped[int] = mapped_column(nullable=True)
    processed: Mapped[int] = mapped_column(nullable=True)
    skipped: Mapped[int] = mapped_column(nullable=True)
    failed: Mapped[int] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column()


#------------------------#
#       Functions        #
#------------------------#
# TODO: Add transaction logic for safe db operations
def post_exists(source, source_post_id):
    with Session(engine) as session:
        source_post_known = select(Posts).where(Posts.source == source, Posts.source_post_id == source_post_id)
        result = session.execute(source_post_known).first()

    return bool(result is not None)

def md5_exists(md5):
    with Session(engine) as session:
        md5_existing = select(Posts).where(Posts.md5 == md5)
        result = session.execute(md5_existing).first()
        
    return bool(result is not None)

def store_post(source, source_post_id, szurubooru_post_id, md5, image_url, status):
    with Session(engine) as session:
        new_post = Posts(
            source=source, 
            source_post_id=source_post_id, 
            szurubooru_post_id=szurubooru_post_id, 
            md5=md5, 
            image_url=image_url, 
            status=status
            )

        session.add(new_post)
        session.commit()
        return new_post.id

def tag_exists(tag_name):
    with Session(engine) as session:
        tag_name_exists = select(Tags).where(Tags.tag == tag_name)
        result = session.execute(tag_name_exists).first()

    return bool(result is not None)

def store_tag(tag_name, tag_type):
    with Session(engine) as session:
        save_tag = Tags(tag=tag_name, type=tag_type)

        session.add(save_tag)
        session.commit()

def add_szurubooru_post_id(id, szurubooru_post_id):
    with Session(engine) as session:
        find_id = select(Posts).where(Posts.id == id)
        id_found = session.execute(find_id).scalar_one()
        id_found.szurubooru_post_id = szurubooru_post_id
        session.commit()

def update_post_status(source, source_post_id, status):
    with Session(engine) as session:
            find_post = select(Posts).where(Posts.source == source, Posts.source_post_id == source_post_id)
            post_found = session.execute(find_post).scalar_one()
            post_found.status = status
            session.commit()

def get_runs():
    with Session(engine) as session:
        get_entries = select(Runs).order_by(Runs.created_at.desc())
        result = session.execute(get_entries).scalars().all()
    return result

def create_run(uuid, source, tags, blacklist_tags, rating, status):
    with Session(engine) as session:
        run = Runs(
            uuid=uuid,
            source=source,
            tags=tags,
            blacklist_tags=blacklist_tags,
            rating=rating,
            status=status
            )
        
        session.add(run)
        session.commit()

        return run.id

def update_run(id, total, processed, skipped, failed, status):
    with Session(engine) as session:
        find_run = select(Runs).where(Runs.id == id)
        run_found = session.execute(find_run).scalar_one()
        run_found.total = total
        run_found.processed = processed
        run_found.skipped = skipped
        run_found.failed = failed
        run_found.status = status
        session.commit()

Base.metadata.create_all(engine)
