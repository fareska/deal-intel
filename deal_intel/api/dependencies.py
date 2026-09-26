from collections.abc import Iterator

from sqlalchemy.orm import Session

from deal_intel.db.session import get_session_factory


def get_db_session() -> Iterator[Session]:
    with get_session_factory()() as session:
        yield session
