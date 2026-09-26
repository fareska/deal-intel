from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import deal_intel.db.models  # noqa: F401  registers every table on Base.metadata
from deal_intel.config import get_settings
from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.evidence import CHUNK_SOURCE_TYPES, PackChunk, chunk_kind_of
from deal_intel.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = get_settings().test_database_url
    if url is None:
        pytest.skip("TEST_DATABASE_URL is not set")
    return str(url)


@pytest.fixture(scope="session")
def alembic_config(test_database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    # configparser treats % as interpolation, so a URL-encoded password must escape it.
    config.set_main_option("sqlalchemy.url", test_database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def migrated_engine(alembic_config: Config, test_database_url: str) -> Iterator[Engine]:
    command.upgrade(alembic_config, "head")
    engine = create_engine(test_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    with Session(migrated_engine) as session:
        yield session
    truncate_all_tables(migrated_engine)


@pytest.fixture
def session_factory(db_session: Session) -> sessionmaker[Session]:
    """Short committed sessions, as the tracer and LLM client use; `db_session` truncates after."""
    return sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)


type PackChunkFactory = Callable[[str, str], PackChunk]


@pytest.fixture(scope="session")
def pack_chunk() -> PackChunkFactory:
    """Builds a pack chunk from an id and its text, for tests that need evidence without a DB."""

    def build(chunk_id: str, text_value: str) -> PackChunk:
        kind = chunk_kind_of(chunk_id)
        return PackChunk(
            chunk_id=chunk_id,
            citation=chunk_id,
            kind=kind,
            source_type=CHUNK_SOURCE_TYPES[kind],
            opportunity_id="OPP-1003",
            account_id="ACC-2003",
            access_level=AccessLevel.STANDARD,
            event_date=None,
            author_or_speakers=None,
            text=text_value,
            content_hash=chunk_id,
            score=1.0,
            estimated_tokens=len(text_value.split()) or 1,
        )

    return build


def truncate_all_tables(engine: Engine) -> None:
    table_names = [table.name for table in Base.metadata.sorted_tables]
    if not table_names:
        return
    quote = engine.dialect.identifier_preparer.quote
    joined = ", ".join(quote(name) for name in table_names)
    with engine.begin() as connection:
        # Names come from Base.metadata and are quoted, never from input.
        connection.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))  # noqa: S608
