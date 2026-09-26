import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

TSV_DELIMITER = "\t"
# Line 1 holds the header, so the first data row is line 2.
FIRST_DATA_LINE = 2


class TsvRowError(ValueError):
    def __init__(self, path: Path, line_number: int, cause: ValidationError) -> None:
        super().__init__(f"{path}:{line_number}: {cause}")
        self.path = path
        self.line_number = line_number


@dataclass(frozen=True)
class RawRow:
    line_number: int
    values: dict[str, str | None]


def read_rows(path: Path) -> Iterator[RawRow]:
    with path.open(newline="", encoding="utf-8") as handle:
        # Cells are never quoted, so a quote character inside a note must stay literal.
        reader = csv.DictReader(handle, delimiter=TSV_DELIMITER, quoting=csv.QUOTE_NONE)
        for line_number, values in enumerate(reader, start=FIRST_DATA_LINE):
            yield RawRow(line_number, values)


def parse_rows[ModelT: BaseModel](path: Path, model: type[ModelT]) -> list[ModelT]:
    parsed: list[ModelT] = []
    for row in read_rows(path):
        try:
            parsed.append(model.model_validate(row.values))
        except ValidationError as error:
            raise TsvRowError(path, row.line_number, error) from error
    return parsed
