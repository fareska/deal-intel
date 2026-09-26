"""Authored Slack-style account-team updates, their validation, and the TSV writer.

The content is authored here and frozen, so the dataset is identical on every run. Each update
is grounded in specific calls; docs/PLAN.md section 6 lists the grounding of every row.
"""

import csv
import io
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import Field, TypeAdapter

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.reference import (
    SLACK_UPDATE_ID_PATTERN,
    GongCallSummary,
    SlackAuthorRole,
    SlackUpdate,
)
from deal_intel.permissions.gate import baseline_access_level
from deal_intel.retrieval.dataset import DatasetFile
from deal_intel.retrieval.reference import ReferenceData, read_reference_data
from deal_intel.retrieval.tsv import TSV_DELIMITER, parse_rows

SYNTHETIC_NOTICE = "SYNTHETIC: generated for the exam dataset; no real people, companies, or data"
SLACK_COLUMNS: tuple[str, ...] = tuple(SlackUpdate.model_fields)
PRICING_KEYWORDS: tuple[str, ...] = ("discount", "reduction", "concession", "%")
FORBIDDEN_CHARACTERS: tuple[str, ...] = ("@", "\t", "\n", "\r")
# Seven or more digits with at most one separator between them; "05-04 to 05-08" stays clear.
PHONE_LIKE = re.compile(r"\+?\d(?:[\s().-]?\d){6,}")
OPPORTUNITY_ID_PREFIX = "OPP-"
UPDATE_ID_PREFIX = "SLK-"


class Channel(StrEnum):
    NORTHSTAR = "#acct-northstar"
    MERIDIAN = "#acct-meridian"
    ECLIPSE = "#acct-eclipse-restricted"


class UpdateKind(StrEnum):
    REINFORCES = "reinforces"
    ADDS_CONTEXT = "adds_context"
    CONFLICTS = "conflicts"


@dataclass(frozen=True)
class AccountThread:
    opportunity_id: str
    account_id: str
    channel: Channel


NORTHSTAR = AccountThread("OPP-1001", "ACC-2001", Channel.NORTHSTAR)
MERIDIAN = AccountThread("OPP-1002", "ACC-2002", Channel.MERIDIAN)
ECLIPSE = AccountThread("OPP-1003", "ACC-2003", Channel.ECLIPSE)


class SlackGoldenLabel(StrictModel):
    update_id: str = Field(pattern=SLACK_UPDATE_ID_PATTERN)
    kind: UpdateKind
    expected_section: str
    expected_effect: str
    keywords: list[str] = Field(min_length=1)


class SlackDatasetError(ValueError):
    def __init__(self, violations: Sequence[str]) -> None:
        super().__init__("Slack dataset is invalid:\n" + "\n".join(violations))
        self.violations = list(violations)


def update_id_prefix(opportunity_id: str) -> str:
    return f"{UPDATE_ID_PREFIX}{opportunity_id.removeprefix(OPPORTUNITY_ID_PREFIX)}-"


def authored(
    thread: AccountThread,
    sequence: int,
    update_date: date,
    author_role: SlackAuthorRole,
    level: AccessLevel,
    text: str,
) -> SlackUpdate:
    return SlackUpdate(
        update_id=f"{update_id_prefix(thread.opportunity_id)}{sequence:02d}",
        opportunity_id=thread.opportunity_id,
        account_id=thread.account_id,
        update_date=update_date,
        channel=thread.channel.value,
        author_role=author_role,
        synthetic_notice=SYNTHETIC_NOTICE,
        source_access_level=level,
        update_text=text,
    )


AUTHORED_UPDATES: tuple[SlackUpdate, ...] = (
    authored(
        NORTHSTAR,
        1,
        date(2026, 4, 27),
        SlackAuthorRole.AE,
        AccessLevel.STANDARD,
        "Recap of the 04-24 document review with Iris Calder and Amara Quinn: procurement raised"
        " no new commercial asks, legal only needs the data-retention policy excerpt, and"
        " procurement expects the signature package next. Owner matrix and payment schedule"
        " are still due 04-28.",
    ),
    authored(
        NORTHSTAR,
        2,
        date(2026, 4, 29),
        SlackAuthorRole.CSM,
        AccessLevel.STANDARD,
        "Iris Calder is out of office 05-04 to 05-08. Her deputy can receive the signature"
        " package but cannot sign. Finance approval of the payment schedule needs to land"
        " before 05-04 or signature slips toward the 05-17 close.",
    ),
    authored(
        NORTHSTAR,
        3,
        date(2026, 5, 2),
        SlackAuthorRole.SE,
        AccessLevel.STANDARD,
        "Pavel Stone now wants the two legacy-appliance sites migrated before the pilot sites,"
        " the reverse of the pilot-first sequencing agreed in the March planning session."
        " Unclear whether Marco Devlin agrees.",
    ),
    authored(
        MERIDIAN,
        1,
        date(2026, 4, 28),
        SlackAuthorRole.SE,
        AccessLevel.STANDARD,
        "Clara Esteves says plant readiness sign-off for the first cutover factory sits with a"
        " site IT lead who is not in our contact list. They asked for on-site support during"
        " cutover week.",
    ),
    authored(
        MERIDIAN,
        2,
        date(2026, 5, 1),
        SlackAuthorRole.AE,
        AccessLevel.STANDARD,
        "Proof closeout pack went to Julian Maro's office last week, I consider that item closed.",
    ),
    authored(
        MERIDIAN,
        3,
        date(2026, 5, 4),
        SlackAuthorRole.CSM,
        AccessLevel.STANDARD,
        "Lena Frost reconfirmed that finance approves only a staged payment tied to acceptance"
        " of the closeout pack, with capped enablement.",
    ),
    authored(
        ECLIPSE,
        1,
        date(2026, 4, 29),
        SlackAuthorRole.AE,
        AccessLevel.SENSITIVE_PRICING,
        "Darin Holt followed up in writing and still wants the larger reduction or a shorter"
        " term modelled. Reminded him the aggressive option is unapproved and internal-only.",
    ),
    authored(
        ECLIPSE,
        2,
        date(2026, 5, 3),
        SlackAuthorRole.AE,
        AccessLevel.SENSITIVE_PRICING,
        "Heard from a colleague that Deal Desk verbally okayed a mid-teens discount for"
        " Eclipse, nothing in writing yet.",
    ),
    authored(
        ECLIPSE,
        3,
        date(2026, 5, 6),
        SlackAuthorRole.CSM,
        AccessLevel.RESTRICTED,
        "Priya Sato's office set the board sponsor review for 05-21, so the internal package"
        " comparison must be final by 05-19. Research IT exception-owner confirmation from"
        " Mateo Ruan is still outstanding.",
    ),
)


def write_slack_dataset(data_root: Path) -> Path:
    """Validates the authored updates against the dataset files, then writes the TSV.

    Reads files rather than the database, so it runs before any ingest.
    """
    summaries = parse_rows(data_root / DatasetFile.GONG_SUMMARIES, GongCallSummary)
    validate_updates(AUTHORED_UPDATES, read_reference_data(data_root), summaries)
    path = data_root / DatasetFile.SLACK_UPDATES
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_tsv(AUTHORED_UPDATES), encoding="utf-8")
    return path


def render_tsv(updates: Iterable[SlackUpdate]) -> str:
    buffer = io.StringIO()
    # Validation guarantees no cell holds a tab or line break, so no cell ever needs quoting.
    writer = csv.writer(
        buffer,
        delimiter=TSV_DELIMITER,
        quoting=csv.QUOTE_NONE,
        quotechar=None,
        lineterminator="\n",
    )
    writer.writerow(SLACK_COLUMNS)
    for update in updates:
        cells = update.model_dump(mode="json")
        writer.writerow(cells[column] for column in SLACK_COLUMNS)
    return buffer.getvalue()


def validate_updates(
    updates: Sequence[SlackUpdate],
    reference: ReferenceData,
    summaries: Sequence[GongCallSummary],
) -> None:
    latest_calls = latest_call_dates(summaries)
    violations = duplicate_id_violations(updates)
    for update in updates:
        violations.extend(update_violations(update, reference, latest_calls))
    if violations:
        raise SlackDatasetError(violations)


def latest_call_dates(summaries: Iterable[GongCallSummary]) -> dict[str, date]:
    latest: dict[str, date] = {}
    for summary in summaries:
        current = latest.get(summary.opportunity_id)
        if current is None or summary.call_date > current:
            latest[summary.opportunity_id] = summary.call_date
    return latest


def duplicate_id_violations(updates: Iterable[SlackUpdate]) -> list[str]:
    counts = Counter(update.update_id for update in updates)
    return [f"{update_id}: duplicate update_id" for update_id, count in counts.items() if count > 1]


def update_violations(
    update: SlackUpdate, reference: ReferenceData, latest_calls: Mapping[str, date]
) -> list[str]:
    opportunity = reference.opportunities.get(update.opportunity_id)
    if opportunity is None:
        return [f"{update.update_id}: unknown opportunity {update.opportunity_id}"]
    account = reference.accounts[opportunity.account_id]
    latest_call = latest_calls.get(update.opportunity_id)
    checks = [
        (
            update.account_id == opportunity.account_id,
            "account_id does not match the opportunity",
        ),
        (
            update.update_id.startswith(update_id_prefix(update.opportunity_id)),
            "update_id does not carry the opportunity number",
        ),
        (
            latest_call is None or update.update_date > latest_call,
            f"update_date is not after the latest call ({latest_call})",
        ),
        (
            update.update_date < opportunity.close_date,
            f"update_date is not before the close date ({opportunity.close_date})",
        ),
        (
            update.source_access_level >= baseline_access_level(opportunity, account),
            "source_access_level is below the account's minimum level",
        ),
        (
            not mentions_pricing(update.update_text)
            or update.source_access_level == AccessLevel.SENSITIVE_PRICING,
            "pricing content must be sensitive_pricing",
        ),
        (
            update.synthetic_notice == SYNTHETIC_NOTICE,
            "synthetic_notice is not the standard notice",
        ),
        (
            not has_forbidden_character(update),
            "contains '@', a tab, or a line break",
        ),
        (
            PHONE_LIKE.search(update.update_text) is None,
            "contains a phone-like digit group",
        ),
    ]
    return [f"{update.update_id}: {problem}" for passed, problem in checks if not passed]


def mentions_pricing(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in PRICING_KEYWORDS)


def has_forbidden_character(update: SlackUpdate) -> bool:
    cells = update.model_dump(mode="json").values()
    return any(character in str(cell) for cell in cells for character in FORBIDDEN_CHARACTERS)


def load_golden_labels(path: Path) -> list[SlackGoldenLabel]:
    return TypeAdapter(list[SlackGoldenLabel]).validate_json(path.read_bytes())
