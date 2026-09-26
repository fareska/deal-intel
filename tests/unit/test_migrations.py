from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine


def test_upgrade_to_head_is_idempotent(alembic_config: Config, migrated_engine: Engine) -> None:
    command.upgrade(alembic_config, "head")

    with migrated_engine.connect() as connection:
        current_heads = MigrationContext.configure(connection).get_current_heads()
    assert current_heads == tuple(ScriptDirectory.from_config(alembic_config).get_heads())
