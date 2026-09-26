from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from deal_intel.db.base import Base


def test_upgrade_to_head_is_idempotent(alembic_config: Config, migrated_engine: Engine) -> None:
    command.upgrade(alembic_config, "head")

    with migrated_engine.connect() as connection:
        current_heads = MigrationContext.configure(connection).get_current_heads()
    assert current_heads == tuple(ScriptDirectory.from_config(alembic_config).get_heads())


def test_migrated_schema_matches_the_models(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == []
