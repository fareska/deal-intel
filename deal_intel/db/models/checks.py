from collections.abc import Iterable
from enum import StrEnum

from sqlalchemy import CheckConstraint


def values_check(column: str, allowed: Iterable[StrEnum], name: str) -> CheckConstraint:
    # Values come from the contract enums, never from input, so formatting them into DDL is safe.
    quoted = ", ".join(f"'{member.value}'" for member in allowed)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)
