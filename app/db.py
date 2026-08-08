from datetime import datetime

from sqlalchemy import (
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app import config

DB_USER = config.DB_USER
DB_PASS = config.DB_PASS
DB_HOST = config.DB_HOST
DB_PORT = config.DB_PORT
DB_NAME = config.DB_NAME

engine = create_engine(f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}", echo=True)

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
    updated_at: Mapped[datetime] = mapped_column(onupdate=datetime.utcnow)
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
    updated_at: Mapped[datetime] = mapped_column(onupdate=datetime.utcnow)
    tag: Mapped[str] = mapped_column()
    type: Mapped[int] = mapped_column()

#------------------------#
#       Functions        #
#------------------------#
# TODO: Add transaction logic for safe db operations
def post_exists(source, source_post_id):
    with Session(engine) as session:
        source_post_known = select(Posts).where(Posts.source == source, Posts.source_post_id == source_post_id)
        result = session.execute(source_post_known).first()

    return bool(result is not None)

def store_post(source, source_post_id, szurubooru_post_id, md5, image_url, status):
    with Session(engine) as session:
        new_post = Posts(source=source, 
        source_post_id=source_post_id, 
        szurubooru_post_id=szurubooru_post_id, 
        md5=md5, 
        image_url=image_url, 
        status=status)

        session.add(new_post)
        session.commit()

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

def update_post_status(source, source_post_id, status):
    with Session(engine) as session:
            find_post = select(Posts).where(Posts.source == source, Posts.source_post_id == source_post_id)
            post_found = session.execute(find_post).scalar_one()
            post_found.status = status
            session.commit()

Base.metadata.create_all(engine)
