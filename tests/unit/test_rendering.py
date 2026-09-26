"""The rendered brief: section order, citations, evidence listing, labels, approved language,
access level, and replay."""

import re
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.approvals import ApproverRole, Decision
from deal_intel.contracts.brief import SECTION_HEADINGS, ApprovalLabel, BriefSource, LabelKind
from deal_intel.db.models import ApprovalRow
from deal_intel.guardrails.canaries import build_canary_set, surface_leaks
from deal_intel.orchestration.outputs import stored_retrieval, stored_scope
from deal_intel.orchestration.persistence import successful_outputs
from deal_intel.policy.engine import decide
from deal_intel.rendering.brief import ReplayUnavailable, replay
from deal_intel.rendering.labels import label_text

CITATION = re.compile(
    r"^source=synthetic_data/[a-z_/]+\.(?:tsv|md), [a-z_]+=[A-Za-z0-9-]+(?:, segment=\d+)?$"
)
CLAIM_SECTIONS = (
    "deal_snapshot",
    "executive_summary",
    "buyer_goals",
    "stakeholder_map",
    "negotiation_state",
    "next_actions",
)
ALTERNATIVE_NOTE = "pricing:PN-4005"
DEAL_DESK_APPROVER = "USR-5005"


def claims(value: object) -> Iterator[dict]:
    """Every cited line in a brief's JSON: anything that carries `citations`."""
    if isinstance(value, dict):
        if "citations" in value:
            yield value
        for item in value.values():
            yield from claims(item)
    elif isinstance(value, list):
        for item in value:
            yield from claims(item)


@pytest.fixture
def opp_1001_brief(run_bench):
    record = run_bench.run(run_bench.agents("OPP-1001"), "USR-5001", "OPP-1001")
    return run_bench.briefs(record.run_id)[-1]


@pytest.fixture
def opp_1003_run(run_bench) -> str:
    return run_bench.run(run_bench.agents("OPP-1003"), "USR-5003", "OPP-1003").run_id


def test_markdown_has_the_nine_headings_in_order(opp_1001_brief) -> None:
    lines = opp_1001_brief.markdown.splitlines()

    assert lines[0] == "# Negotiation Brief: OPP-1001"
    assert [line[3:] for line in lines if line.startswith("## ")] == list(SECTION_HEADINGS)


def test_every_claim_line_carries_standard_citations(opp_1001_brief) -> None:
    body = opp_1001_brief.json
    lines = [claim for section in CLAIM_SECTIONS for claim in claims(body[section])]

    assert lines
    for claim in lines:
        assert claim["citations"]
        assert all(CITATION.match(citation) for citation in claim["citations"])
    for claim in claims(body["executive_summary"]):
        rendered = " ".join([claim["text"], *(f"[{c}]" for c in claim["citations"])])
        assert f"- {rendered}" in opp_1001_brief.markdown.splitlines()


def test_source_evidence_lists_each_cited_chunk_once(opp_1001_brief) -> None:
    body = opp_1001_brief.json
    cited = {
        citation
        for section in CLAIM_SECTIONS
        for claim in claims(body[section])
        for citation in claim["citations"]
    }
    listed = [entry["citation"] for entry in body["source_evidence"]["entries"]]

    assert len(listed) == len(set(listed))
    assert set(listed) == cited
    assert all(len(entry["excerpt"]) <= 200 for entry in body["source_evidence"]["entries"])


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (
            ApprovalLabel(kind=LabelKind.PENDING, role=ApproverRole.DEAL_DESK),
            "[PENDING APPROVAL: deal_desk] INTERNAL ONLY",
        ),
        (
            ApprovalLabel(
                kind=LabelKind.APPROVED, actor_user_id="USR-5005", decided_on=date(2026, 5, 2)
            ),
            "[APPROVED by USR-5005 on 2026-05-02]",
        ),
        (ApprovalLabel(kind=LabelKind.REJECTED, role=ApproverRole.DEAL_DESK), "[REJECTED]"),
        (
            ApprovalLabel(kind=LabelKind.ESCALATED, role=ApproverRole.LEGAL),
            "[ESCALATED: no eligible approver for legal]",
        ),
        (ApprovalLabel(kind=LabelKind.EXPIRED, role=ApproverRole.DEAL_DESK), "[APPROVAL EXPIRED]"),
        (
            ApprovalLabel(kind=LabelKind.NOT_REQUESTABLE),
            "[REQUIRES APPROVAL; you are not permitted to request it]",
        ),
    ],
)
def test_labels_use_the_exact_wording(label: ApprovalLabel, expected: str) -> None:
    assert label_text(label) == expected


def test_pending_brief_labels_items_internal_only(run_bench, opp_1003_run: str) -> None:
    markdown = run_bench.briefs(opp_1003_run)[-1].markdown

    assert "[PENDING APPROVAL: deal_desk] INTERNAL ONLY" in markdown
    assert "[ESCALATED: no eligible approver for sales_leader]" in markdown
    assert "We can offer" not in markdown


def test_approved_item_gets_templated_customer_language(run_bench, opp_1003_run: str) -> None:
    with run_bench.session_factory() as session:
        approval = session.scalars(
            select(ApprovalRow.approval_id).where(
                ApprovalRow.run_id == opp_1003_run, ApprovalRow.subject_id == ALTERNATIVE_NOTE
            )
        ).one()
    with run_bench.session_factory.begin() as session:
        decide(session, approval, DEAL_DESK_APPROVER, Decision.APPROVED, "ok", run_bench.clock())

    brief = run_bench.briefs(opp_1003_run)[-1].json
    [item] = [
        item
        for item in brief["next_actions"]["policy_items"]
        if item["subject_id"] == ALTERNATIVE_NOTE
    ]
    assert item["customer_language"] == (
        "We can offer a price adjustment of 12 percent on the proposed annual contract value, "
        "subject to final contract terms. We can offer a renewal price change of -3 percent for "
        "this term, subject to final contract terms."
    )


def test_max_access_level_comes_from_the_cited_chunks(
    run_bench, opp_1001_brief, opp_1003_run: str
) -> None:
    assert opp_1001_brief.max_access_level == AccessLevel.STANDARD.value
    sensitive = run_bench.briefs(opp_1003_run)[-1]
    assert sensitive.max_access_level == AccessLevel.SENSITIVE_PRICING.value
    assert sensitive.json["metadata"]["max_access_level"] == AccessLevel.SENSITIVE_PRICING.value


def test_replay_twice_writes_new_versions_with_identical_markdown(
    run_bench, opp_1003_run: str
) -> None:
    original = run_bench.briefs(opp_1003_run)[-1]
    replays = []
    for _ in range(2):
        run_bench.clock.advance(timedelta(minutes=5))
        with run_bench.session_factory.begin() as session:
            replays.append(replay(session, opp_1003_run, run_bench.clock()))

    briefs = run_bench.briefs(opp_1003_run)
    assert [brief.version for brief in briefs] == [1, 2, 3]
    assert [brief.source for brief in briefs[1:]] == [BriefSource.REPLAY.value] * 2
    assert {brief.markdown for brief in briefs} == {original.markdown}
    assert replays[0].markdown.encode() == replays[1].markdown.encode()
    assert briefs[1].rendered_at != briefs[2].rendered_at


def test_replay_needs_a_rendered_brief(run_bench) -> None:
    record = run_bench.run(run_bench.agents("OPP-1003"), "USR-5007", "OPP-1003")

    with pytest.raises(ReplayUnavailable), run_bench.session_factory.begin() as session:
        replay(session, record.run_id, run_bench.clock())


@pytest.mark.parametrize("user_id", ["USR-5001", "USR-5007"])
def test_canary_scan_of_an_allowed_brief_finds_nothing(run_bench, user_id: str) -> None:
    record = run_bench.run(run_bench.agents("OPP-1001"), user_id, "OPP-1001")
    brief = run_bench.briefs(record.run_id)[-1]

    with run_bench.session_factory() as session:
        outputs = successful_outputs(session, record.run_id)
        canaries = build_canary_set(
            session,
            stored_retrieval(outputs).snapshot_id,
            stored_scope(outputs),
            [user_id, "OPP-1001"],
        )
        hits = surface_leaks(session, record.run_id, [brief.markdown, str(brief.json)], canaries)

    assert canaries.size() > 0
    assert hits == []
