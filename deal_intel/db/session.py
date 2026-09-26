from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.config import get_settings

AUTOCOMMIT = "AUTOCOMMIT"


@lru_cache
def get_engine() -> Engine:
    return create_engine(str(get_settings().database_url), pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def autocommit_factory(session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    """Sessions whose every statement commits at once, for reads that surround a model call:
    a call that takes a minute must not hold a transaction, or its locks, for that minute."""
    engine: Engine = session_factory.kw["bind"]
    return sessionmaker(
        bind=engine.execution_options(isolation_level=AUTOCOMMIT), expire_on_commit=False
    )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commits when the block exits normally, rolls back on an exception."""
    with get_session_factory().begin() as session:
        yield session
