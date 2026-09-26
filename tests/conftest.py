from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

import deal_intel.db.models  # noqa: F401  registers every table on Base.metadata
from deal_intel.config import get_settings
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


def truncate_all_tables(engine: Engine) -> None:
    table_names = [table.name for table in Base.metadata.sorted_tables]
    if not table_names:
        return
    quote = engine.dialect.identifier_preparer.quote
    joined = ", ".join(quote(name) for name in table_names)
    with engine.begin() as connection:
        # Names come from Base.metadata and are quoted, never from input.
        connection.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))  # noqa: S608
