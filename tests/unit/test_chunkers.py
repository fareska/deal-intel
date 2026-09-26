import logging
import re
import shutil
from collections import Counter
from datetime import date
from itertools import pairwise
from pathlib import Path

import pytest

from deal_intel.contracts.access import AccessLevel, SourceType
from deal_intel.contracts.evidence import CHUNK_ID_PATTERN, ChunkKind, EvidenceChunk
from deal_intel.retrieval import ingest
from deal_intel.retrieval.chunkers.base import SourceFormatError
from deal_intel.retrieval.chunkers.policy import parse_policy_rules
from deal_intel.retrieval.chunkers.salesforce import chunk_accounts
from deal_intel.retrieval.chunkers.slack import chunk_slack_updates
from deal_intel.retrieval.chunkers.transcripts import (
    WINDOW_TURNS,
    parse_transcript,
    window_turns,
)
from deal_intel.retrieval.dataset import CITATION_ROOT, DatasetFile, transcript_paths
from deal_intel.retrieval.ingest import DuplicateChunkId, build_chunks, build_context
from deal_intel.retrieval.reference import ReferenceData
from deal_intel.retrieval.sensitivity import SensitivityRule

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ingest"
WINDOW_FIXTURE = FIXTURES / "OPP-9999_CALL-999.md"
MAX_WINDOW_TURNS = 8
MIN_TRANSCRIPT_WINDOWS = 9

EXPECTED_COUNTS = {
    ChunkKind.SFDC_OPP: 3,
    ChunkKind.SFDC_ACCOUNT: 3,
    ChunkKind.CONTACT: 15,
    ChunkKind.GONG_SUMMARY: 27,
    ChunkKind.PRICING: 5,
    ChunkKind.POLICY: 10,
    ChunkKind.SLACK: 9,
}

EXPECTED_CITATIONS = {
    "sfdc_opp:OPP-1001": (
        "source=synthetic_data/salesforce/opportunities.tsv, opportunity_id=OPP-1001"
    ),
    "sfdc_account:ACC-2001": "source=synthetic_data/salesforce/accounts.tsv, account_id=ACC-2001",
    "contact:CON-3001": "source=synthetic_data/salesforce/contacts.tsv, contact_id=CON-3001",
    "gong_summary:CALL-008": (
        "source=synthetic_data/gong/gong_call_summaries.tsv, call_id=CALL-008"
    ),
    "transcript:CALL-027:1": (
        "source=synthetic_data/gong/transcripts/OPP-1003_CALL-027.md, call_id=CALL-027, segment=1"
    ),
    "pricing:PN-4004": "source=synthetic_data/pricing/pricing_notes.tsv, pricing_note_id=PN-4004",
    "policy:rule-3": "source=synthetic_data/policies/deal_desk_policy.md, rule=3",
    "slack:SLK-1003-02": (
        "source=synthetic_data/slack/account_team_updates.tsv, update_id=SLK-1003-02"
    ),
}

EXPECTED_LEVELS = {
    "pricing:PN-4001": AccessLevel.STANDARD,
    "pricing:PN-4002": AccessLevel.STANDARD,
    "pricing:PN-4003": AccessLevel.STANDARD,
    "pricing:PN-4004": AccessLevel.SENSITIVE_PRICING,
    "pricing:PN-4005": AccessLevel.SENSITIVE_PRICING,
    "slack:SLK-1003-01": AccessLevel.SENSITIVE_PRICING,
    "slack:SLK-1003-02": AccessLevel.SENSITIVE_PRICING,
    "slack:SLK-1003-03": AccessLevel.RESTRICTED,
    "slack:SLK-1001-01": AccessLevel.STANDARD,
    "sfdc_opp:OPP-1003": AccessLevel.RESTRICTED,
    "sfdc_account:ACC-2003": AccessLevel.RESTRICTED,
    "gong_summary:CALL-021": AccessLevel.SENSITIVE_PRICING,
    "policy:rule-9": AccessLevel.STANDARD,
}


def by_id(chunks: list[EvidenceChunk]) -> dict[str, EvidenceChunk]:
    return {chunk.chunk_id: chunk for chunk in chunks}


def of_kind(chunks: list[EvidenceChunk], kind: ChunkKind) -> list[EvidenceChunk]:
    return [chunk for chunk in chunks if chunk.kind == kind]


def copy_dataset(source: Path, destination: Path) -> Path:
    root = destination / CITATION_ROOT
    shutil.copytree(source, root)
    return root


def test_chunk_counts_per_kind(evidence_chunks: list[EvidenceChunk]) -> None:
    counts = Counter(chunk.kind for chunk in evidence_chunks)

    assert {kind: counts[kind] for kind in EXPECTED_COUNTS} == EXPECTED_COUNTS
    assert counts[ChunkKind.TRANSCRIPT] >= MIN_TRANSCRIPT_WINDOWS


def test_every_transcript_yields_bounded_windows(
    evidence_chunks: list[EvidenceChunk], dataset_root: Path
) -> None:
    windows = of_kind(evidence_chunks, ChunkKind.TRANSCRIPT)
    calls_with_windows = {chunk.source_id for chunk in windows}

    assert len(calls_with_windows) == len(transcript_paths(dataset_root))
    assert all(chunk.metadata["turn_count"] <= MAX_WINDOW_TURNS for chunk in windows)
    assert all(chunk.text.startswith("Call CALL-") for chunk in windows)


@pytest.mark.parametrize(("chunk_id", "citation"), sorted(EXPECTED_CITATIONS.items()))
def test_citation_strings(
    evidence_chunks: list[EvidenceChunk], chunk_id: str, citation: str
) -> None:
    assert by_id(evidence_chunks)[chunk_id].citation() == citation


@pytest.mark.parametrize(("chunk_id", "level"), sorted(EXPECTED_LEVELS.items()))
def test_access_levels(
    evidence_chunks: list[EvidenceChunk], chunk_id: str, level: AccessLevel
) -> None:
    assert by_id(evidence_chunks)[chunk_id].access_level == level


def test_restricted_opportunity_evidence_is_never_standard(
    evidence_chunks: list[EvidenceChunk],
) -> None:
    eclipse = [
        chunk
        for chunk in evidence_chunks
        if chunk.account_id == "ACC-2003" or chunk.opportunity_id == "OPP-1003"
    ]

    assert eclipse
    assert all(chunk.access_level >= AccessLevel.RESTRICTED for chunk in eclipse)
    call_027 = [chunk for chunk in eclipse if chunk.chunk_id.startswith("transcript:CALL-027:")]
    assert call_027
    assert all(chunk.access_level == AccessLevel.SENSITIVE_PRICING for chunk in call_027)


def test_every_chunk_is_well_formed(evidence_chunks: list[EvidenceChunk]) -> None:
    for chunk in evidence_chunks:
        assert re.fullmatch(CHUNK_ID_PATTERN, chunk.chunk_id)
        assert chunk.source_file.startswith(f"{CITATION_ROOT}/")
        assert chunk.snapshot_id
        assert (chunk.account_id is None) == (chunk.source_type == SourceType.POLICIES)


def test_gong_participants_resolve_to_name_and_title(
    evidence_chunks: list[EvidenceChunk],
) -> None:
    summary = by_id(evidence_chunks)["gong_summary:CALL-001"]

    assert "Elena Voss, Chief Information Security Officer" in summary.text
    assert summary.author_or_speakers is not None
    assert "CON-3001" not in summary.author_or_speakers


def test_contacts_never_carry_email_or_phone(
    evidence_chunks: list[EvidenceChunk], reference: ReferenceData
) -> None:
    for chunk in of_kind(evidence_chunks, ChunkKind.CONTACT):
        contact = reference.contacts[chunk.source_id]
        rendered = f"{chunk.text} {chunk.metadata}"
        assert "@" not in rendered
        assert contact.email not in rendered
        assert contact.phone not in rendered


def test_policy_rules_carry_bare_numbers(evidence_chunks: list[EvidenceChunk]) -> None:
    rules = of_kind(evidence_chunks, ChunkKind.POLICY)

    assert sorted(int(chunk.source_id) for chunk in rules) == list(range(1, 11))
    assert by_id(evidence_chunks)["policy:rule-3"].text.startswith("Deal Desk policy rule 3:")


def test_pricing_metadata_marks_sensitivity(evidence_chunks: list[EvidenceChunk]) -> None:
    chunks = by_id(evidence_chunks)

    assert chunks["pricing:PN-4004"].metadata["sensitive"] is True
    assert chunks["pricing:PN-4001"].metadata["sensitive"] is False
    assert "Requested discount: 18%" in chunks["pricing:PN-4004"].text


def test_event_dates_follow_the_source(evidence_chunks: list[EvidenceChunk]) -> None:
    chunks = by_id(evidence_chunks)

    assert chunks["gong_summary:CALL-027"].event_date == date(2026, 4, 27)
    assert chunks["transcript:CALL-027:1"].event_date == date(2026, 4, 27)
    assert chunks["slack:SLK-1003-02"].event_date == date(2026, 5, 3)
    assert chunks["contact:CON-3001"].event_date == date(2026, 4, 18)
    assert chunks["pricing:PN-4004"].event_date is None
    assert chunks["sfdc_opp:OPP-1001"].event_date is None


def test_rebuilding_gives_identical_hashes(
    dataset_root: Path, sensitivity: SensitivityRule, evidence_chunks: list[EvidenceChunk]
) -> None:
    rebuilt = build_chunks(build_context(dataset_root, "another-snapshot", sensitivity))

    assert [chunk.content_hash for chunk in rebuilt] == [
        chunk.content_hash for chunk in evidence_chunks
    ]


def test_window_rule_merges_a_short_tail() -> None:
    transcript = parse_transcript(WINDOW_FIXTURE)

    assert len(transcript.turns) == 13
    assert [len(window) for window in window_turns(transcript.turns)] == [WINDOW_TURNS, 8]


def test_windows_overlap_by_one_turn() -> None:
    windows = window_turns(list(range(20)))

    assert len(windows) > 1
    assert all(earlier[-1] == later[0] for earlier, later in pairwise(windows))


def test_short_transcript_is_one_window() -> None:
    assert window_turns([1, 2, 3]) == [[1, 2, 3]]


def test_transcript_header_and_turns_are_parsed() -> None:
    transcript = parse_transcript(WINDOW_FIXTURE)

    assert transcript.call_id == "CALL-999"
    assert transcript.opportunity_id == "OPP-9999"
    assert transcript.access_level == AccessLevel.RESTRICTED
    assert transcript.call_date == date(2026, 3, 2)
    second = transcript.turns[1]
    assert second.speaker == "Test Buyer"
    assert second.text == "Turn two has a colon inside the sentence: it must stay in the text."


def test_transcript_without_title_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    path.write_text("Vendor AE: no title line\n")

    with pytest.raises(SourceFormatError, match="first line"):
        parse_transcript(path)


def test_transcript_without_header_field_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    lines = WINDOW_FIXTURE.read_text().splitlines()
    path.write_text("\n".join(line for line in lines if not line.startswith("**Date:**")))

    with pytest.raises(SourceFormatError, match="Date"):
        parse_transcript(path)


def test_malformed_turn_error_does_not_echo_evidence(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    path.write_text(WINDOW_FIXTURE.read_text() + "\nconfidential line without a speaker\n")

    with pytest.raises(SourceFormatError, match="turn 14") as raised:
        parse_transcript(path)

    assert "confidential" not in str(raised.value)


def test_policy_parser_ignores_numbered_lists_outside_the_rules(tmp_path: Path) -> None:
    path = tmp_path / "policy.md"
    path.write_text(
        "# Policy\n\n1. Not a rule.\n\n## Approval Rules\n\n1. First rule.\n2. Second rule.\n\n"
        "## Appendix\n\n3. Not a rule either.\n"
    )

    assert [rule.text for rule in parse_policy_rules(path)] == ["First rule.", "Second rule."]


def test_policy_without_rules_heading_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "policy.md"
    path.write_text("# Policy\n\n1. Not a rule.\n")

    with pytest.raises(SourceFormatError, match="Approval Rules"):
        parse_policy_rules(path)


def test_missing_slack_file_warns_and_yields_nothing(
    synthetic_data: Path,
    tmp_path: Path,
    sensitivity: SensitivityRule,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = copy_dataset(synthetic_data, tmp_path)
    (root / DatasetFile.SLACK_UPDATES).unlink(missing_ok=True)

    with caplog.at_level(logging.WARNING):
        chunks = chunk_slack_updates(build_context(root, "no-slack", sensitivity))

    assert chunks == []
    assert "generate-slack" in caplog.text


def test_duplicate_chunk_ids_are_rejected(
    dataset_root: Path, sensitivity: SensitivityRule, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "CHUNKERS", (chunk_accounts, chunk_accounts))

    with pytest.raises(DuplicateChunkId, match="sfdc_account:ACC-2001"):
        build_chunks(build_context(dataset_root, "duplicate", sensitivity))
