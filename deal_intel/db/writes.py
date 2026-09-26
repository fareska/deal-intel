from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from deal_intel.db.base import Base


def column_values(model: BaseModel) -> dict[str, Any]:
    """Dumps a contract whose field names equal its table's column names."""
    return {name: plain_value(value) for name, value in model.model_dump().items()}


def plain_value(value: object) -> object:
    # psycopg adapts by exact type, so enum members are written as their plain values.
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list | tuple | set | frozenset):
        return [plain_value(item) for item in value]
    return value


def upsert_rows(session: Session, table: type[Base], rows: Sequence[Mapping[str, Any]]) -> None:
    """Inserts rows or refreshes existing ones by primary key, touching only the given columns.

    Refreshing only the given columns keeps generated columns (which Postgres refuses to
    update) and optional columns such as embeddings out of the statement.
    """
    if not rows:
        return
    core_table = table.__table__
    statement = insert(core_table).values(list(rows))
    key_names = [column.name for column in core_table.primary_key.columns]
    refreshed = {name: statement.excluded[name] for name in rows[0] if name not in key_names}
    session.execute(statement.on_conflict_do_update(index_elements=key_names, set_=refreshed))
