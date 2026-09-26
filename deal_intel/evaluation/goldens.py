"""Comparable golden briefs: section structure, citations, labels, and rule ids."""

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from deal_intel.contracts.approvals import RuleId
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.brief import SECTION_HEADINGS, Brief
from deal_intel.evaluation.scenarios import EXPECTED_RULES_PATH, GOLDEN_ROOT, DemoPair
from deal_intel.retrieval.slack_dataset import SlackGoldenLabel, load_golden_labels

SECTION_FIELDS: tuple[str, ...] = (
    "deal_snapshot",
    "executive_summary",
    "buyer_goals",
    "stakeholder_map",
    "negotiation_state",
    "next_actions",
    "missing_information",
    "source_evidence",
    "confidence",
)
HEADING_BY_FIELD = dict(zip(SECTION_FIELDS, SECTION_HEADINGS, strict=True))


class ExpectedRules(StrictModel):
    pricing_note_rules: list[RuleId]


EXPECTED_RULES_FILE = TypeAdapter(dict[str, ExpectedRules])


class GoldenBrief(StrictModel):
    """What regression compares: not free text, which a re-recording may rephrase."""

    sections: dict[str, list[str]]
    labels: list[str]
    rule_ids: list[str]


def golden_path(pair: DemoPair, root: Path = GOLDEN_ROOT) -> Path:
    return root / pair.golden_name


def load_golden(pair: DemoPair, root: Path = GOLDEN_ROOT) -> GoldenBrief:
    return GoldenBrief.model_validate_json(golden_path(pair, root).read_bytes())


def write_golden(pair: DemoPair, brief: Brief, root: Path = GOLDEN_ROOT) -> Path:
    path = golden_path(pair, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(comparable_brief(brief).model_dump_json(indent=2).encode("utf-8") + b"\n")
    return path


def comparable_brief(brief: Brief) -> GoldenBrief:
    return GoldenBrief(
        sections={
            HEADING_BY_FIELD[field]: sorted(collect_citations(getattr(brief, field)))
            for field in SECTION_FIELDS
        },
        labels=sorted(collect_labels(brief)),
        rule_ids=sorted({rule.value for rule in collect_rule_ids(brief)}),
    )


def collect_citations(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, BaseModel):
        for name, field in value:
            if name == "citations":
                found.extend(field)
            else:
                found.extend(collect_citations(field))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_citations(item))
    return found


def collect_labels(brief: Brief) -> list[str]:
    kinds: list[str] = []
    for action in brief.next_actions.actions:
        kinds.extend(label.kind.value for label in action.labels)
    for item in brief.next_actions.policy_items:
        kinds.extend(label.kind.value for label in item.labels)
    return kinds


def collect_rule_ids(brief: Brief) -> list[RuleId]:
    rules: list[RuleId] = []
    for action in brief.next_actions.actions:
        rules.extend(action.rule_ids)
    for item in brief.next_actions.policy_items:
        rules.extend(item.rule_ids)
    return rules


def load_expected_rules(path: Path = EXPECTED_RULES_PATH) -> dict[str, ExpectedRules]:
    return EXPECTED_RULES_FILE.validate_json(path.read_bytes())


def slack_labels(path: Path) -> list[SlackGoldenLabel]:
    return load_golden_labels(path)


def section_text_by_heading(brief: Brief, markdown: str) -> dict[str, str]:
    """Maps each brief heading to the Markdown body under it, plus the JSON dump of the section."""
    bodies = split_markdown_sections(markdown)
    texts = dict(bodies)
    for field, heading in HEADING_BY_FIELD.items():
        dumped = getattr(brief, field).model_dump_json()
        texts[heading] = f"{texts.get(heading, '')}\n{dumped}"
    return texts


def split_markdown_sections(markdown: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current: str | None = None
    lines: list[str] = []
    for line in markdown.splitlines():
        heading = line[3:].strip() if line.startswith("## ") else None
        if heading in SECTION_HEADINGS:
            if current is not None:
                sections.append((current, "\n".join(lines)))
            current = heading
            lines = []
            continue
        if current is not None:
            lines.append(line)
    if current is not None:
        sections.append((current, "\n".join(lines)))
    return sections


def section_is_populated(section: BaseModel) -> bool:
    return any(_value_present(value) for value in section.model_dump().values())


def _value_present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_value_present(item) for item in value.values())
    if isinstance(value, list):
        return any(_value_present(item) for item in value)
    return True
