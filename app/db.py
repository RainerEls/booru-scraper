from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

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

Base.metadata.create_all(engine)