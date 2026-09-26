"""Pure text helpers for the validators: figures, quotes, and whitespace."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

MIN_BARE_DIGITS = 4
MIN_QUOTE_WORDS = 4
BARE_YEARS = range(1900, 2101)
SCALE_EXPONENTS = {
    "thousand": 3,
    "k": 3,
    "million": 6,
    "m": 6,
    "billion": 9,
    "bn": 9,
    "b": 9,
}
WHITESPACE = re.compile(r"\s+")
FIGURE_PATTERN = re.compile(
    r"""
    (?<![\w.$-])                            # not inside an identifier, a decimal, or a figure
    (?P<sign>-)?
    (?P<currency>\$)?
    (?P<integer>\d{1,3}(?:,\d{3})+|\d+)
    (?:\.(?P<fraction>\d+))?
    (?:
        \s?(?P<percent>%|(?i:percent)\b)
      | \s?(?P<scale_word>(?i:thousand|million|billion|bn))\b
      | (?P<scale_letter>[kKmMbB])\b
      | (?![\w-]|\.\d)                      # not an id, a date, or a decimal cut short
    )
    """,
    re.VERBOSE,
)
QUOTE_PATTERN = re.compile(r"[\"“]([^\"“”]+)[\"”]")


class FigureKind(StrEnum):
    AMOUNT = "amount"
    PERCENT = "percent"


@dataclass(frozen=True)
class Figure:
    """A stated number and the precision it was stated with, so `$4.2M` can match `$4,217,500`.

    The sign is dropped: evidence often words a cut as a positive figure ("an 8% reduction")
    where an output writes -8%.
    """

    kind: FigureKind
    value: Decimal
    step: Decimal

    def matches(self, other: "Figure") -> bool:
        """Equal within the coarser of the two stated precisions."""
        return self.kind is other.kind and abs(self.value - other.value) * 2 < max(
            self.step, other.step
        )

    def label(self) -> str:
        number = f"{self.value.normalize():f}"
        return f"{number}%" if self.kind is FigureKind.PERCENT else number


def normalise_whitespace(text: str) -> str:
    return WHITESPACE.sub(" ", text).strip()


@lru_cache(maxsize=4096)
def extract_figures(text: str) -> tuple[Figure, ...]:
    """Currency amounts, scaled amounts, bare numbers of four or more digits, and percentages.
    ISO dates, bare years, and digits inside identifiers such as `OPP-1003` are not figures."""
    figures = (to_figure(match) for match in FIGURE_PATTERN.finditer(text))
    return tuple(figure for figure in figures if figure is not None)


def to_figure(match: re.Match[str]) -> Figure | None:
    digits = match["integer"].replace(",", "")
    fraction = match["fraction"] or ""
    value = Decimal(f"{digits}.{fraction}" if fraction else digits)
    step = Decimal(1).scaleb(-len(fraction))
    if match["percent"]:
        return Figure(FigureKind.PERCENT, value, step)
    scale = match["scale_word"] or match["scale_letter"]
    if scale:
        exponent = SCALE_EXPONENTS[scale.lower()]
        return Figure(FigureKind.AMOUNT, value.scaleb(exponent), step.scaleb(exponent))
    if match["currency"]:
        return Figure(FigureKind.AMOUNT, value, step)
    if len(digits) < MIN_BARE_DIGITS or is_bare_year(match, digits):
        return None
    return Figure(FigureKind.AMOUNT, value, step)


def is_bare_year(match: re.Match[str], digits: str) -> bool:
    written_plainly = match["integer"] == digits and not match["fraction"]
    return written_plainly and len(digits) == MIN_BARE_DIGITS and int(digits) in BARE_YEARS


def is_quotation(span: str) -> bool:
    """Shorter quoted spans are terms of art ("proof pack"), not quotations."""
    return len(span.split()) >= MIN_QUOTE_WORDS


def extract_quotes(text: str) -> list[str]:
    return [match[1] for match in QUOTE_PATTERN.finditer(text) if is_quotation(match[1])]


def unquote_unverified(text: str, is_verified: Callable[[str], bool]) -> tuple[str, list[str]]:
    """Removes the quotation marks around every quotation that fails `is_verified`, keeping the
    words as a paraphrase. Returns the new text and the quotations that failed."""
    failed: list[str] = []

    def replace(match: re.Match[str]) -> str:
        quote = match[1]
        if not is_quotation(quote) or is_verified(quote):
            return match[0]
        failed.append(quote)
        return quote

    return QUOTE_PATTERN.sub(replace, text), failed
