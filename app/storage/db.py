from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings


def create_db_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = create_db_engine()


def init_db(db_engine=None) -> None:
    SQLModel.metadata.create_all(db_engine or engine)


@contextmanager
def get_session(db_engine=None) -> Iterator[Session]:
    with Session(db_engine or engine) as session:
        yield session
