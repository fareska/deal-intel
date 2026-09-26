import re
from dataclasses import dataclass
from pathlib import Path

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.evidence import ChunkKind, EvidenceChunk
from deal_intel.retrieval.chunkers.base import (
    ChunkDraft,
    IngestContext,
    SourceFormatError,
    make_chunk,
)
from deal_intel.retrieval.dataset import DatasetFile

RULES_HEADING = "## Approval Rules"
SECTION_PREFIX = "## "
RULE_LINE = re.compile(r"^(?P<number>\d+)\.\s+(?P<text>.+)$")
RULE_KEY_PREFIX = "rule-"


@dataclass(frozen=True)
class PolicyRule:
    number: int
    text: str


def chunk_policy_rules(context: IngestContext) -> list[EvidenceChunk]:
    rules = parse_policy_rules(context.data_root / DatasetFile.DEAL_DESK_POLICY)
    return [make_chunk(context, rule_draft(rule)) for rule in rules]


def parse_policy_rules(path: Path) -> list[PolicyRule]:
    """Only numbered lines under the rules heading count, so a numbered list elsewhere in the
    document can never pass for a rule."""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    if RULES_HEADING not in lines:
        raise SourceFormatError(path, f"missing the '{RULES_HEADING}' heading")
    rules: list[PolicyRule] = []
    for line in lines[lines.index(RULES_HEADING) + 1 :]:
        if line.startswith(SECTION_PREFIX):
            break
        match = RULE_LINE.match(line)
        if match:
            rules.append(PolicyRule(number=int(match["number"]), text=match["text"]))
    if not rules:
        raise SourceFormatError(path, f"no numbered rules under '{RULES_HEADING}'")
    return rules


def rule_draft(rule: PolicyRule) -> ChunkDraft:
    # The citation reads `rule=3`, so the source id is the bare number.
    return ChunkDraft(
        kind=ChunkKind.POLICY,
        source_key=f"{RULE_KEY_PREFIX}{rule.number}",
        relative_path=DatasetFile.DEAL_DESK_POLICY,
        source_id=str(rule.number),
        opportunity_id=None,
        account_id=None,
        access_level=AccessLevel.STANDARD,
        text=f"Deal Desk policy rule {rule.number}: {rule.text}",
        metadata={"rule_number": rule.number},
    )
