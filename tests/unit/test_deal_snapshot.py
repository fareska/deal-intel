import json
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from deal_intel.agents.deal_snapshot import SNAPSHOT_SOURCE_TYPES, build_deal_snapshot
from deal_intel.contracts.access import AccessScope, Allowed
from deal_intel.contracts.agents.deal_snapshot import DealSnapshot, PricingVisibility
from deal_intel.permissions.gate import authorize
from deal_intel.retrieval.reference import ReferenceData
from deal_intel.retrieval.retriever import ScopedRetriever

REPO_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_MODULE_ROOTS = ("deal_intel.llm", "anthropic")
IMPORT_PROBE = f"""
import json, sys
import deal_intel.agents.deal_snapshot
roots = {FORBIDDEN_MODULE_ROOTS!r}
loaded = [m for m in sys.modules if any(m == r or m.startswith(r + ".") for r in roots)]
print(json.dumps(sorted(loaded)))
"""
HIDDEN_PRICING_MARKERS = ("PN-", "pricing:", "pricing_notes.tsv")

EXPECTED_NOTES: dict[tuple[str, str], list[str]] = {
    ("USR-5001", "OPP-1001"): ["pricing:PN-4001", "pricing:PN-4002"],
    ("USR-5002", "OPP-1002"): ["pricing:PN-4003"],
    ("USR-5003", "OPP-1003"): ["pricing:PN-4004", "pricing:PN-4005"],
    ("USR-5007", "OPP-1001"): [],
}


def scope_for(session: Session, user_id: str, opportunity_id: str) -> AccessScope:
    result = authorize(session, user_id, opportunity_id)
    assert isinstance(result, Allowed)
    return result.scope


def snapshot_for(session: Session, scope: AccessScope) -> DealSnapshot:
    return build_deal_snapshot(session, ScopedRetriever(session, scope)).snapshot


def pricing_ids(snapshot: DealSnapshot) -> list[str]:
    return [note.evidence_id for note in snapshot.pricing_notes]


@pytest.mark.parametrize(("user_id", "opportunity_id"), sorted(EXPECTED_NOTES))
def test_snapshot_copies_reference_rows_and_scoped_pricing(
    ingested_session: Session, reference: ReferenceData, user_id: str, opportunity_id: str
) -> None:
    snapshot = snapshot_for(ingested_session, scope_for(ingested_session, user_id, opportunity_id))

    opportunity = reference.opportunities[opportunity_id]
    expected_notes = EXPECTED_NOTES[(user_id, opportunity_id)]
    assert snapshot.opportunity.record == opportunity
    assert snapshot.account.record == reference.accounts[opportunity.account_id]
    assert pricing_ids(snapshot) == expected_notes
    assert snapshot.pricing_visibility == (
        PricingVisibility.VISIBLE if expected_notes else PricingVisibility.NONE
    )


@pytest.mark.parametrize(("user_id", "opportunity_id"), sorted(EXPECTED_NOTES))
def test_every_block_cites_retrieved_evidence(
    ingested_session: Session, user_id: str, opportunity_id: str
) -> None:
    retriever = ScopedRetriever(
        ingested_session, scope_for(ingested_session, user_id, opportunity_id)
    )

    build = build_deal_snapshot(ingested_session, retriever)

    visible = {chunk.chunk_id: chunk.citation for chunk in retriever.list().chunks}
    snapshot = build.snapshot
    assert snapshot.evidence_ids()[:2] == [
        f"sfdc_opp:{opportunity_id}",
        f"sfdc_account:{snapshot.account.record.account_id}",
    ]
    assert all(visible[block.evidence_id] == block.citation for block in snapshot.cited_blocks())
    assert [record.filters.requested_source_types for record in build.records] == [
        sorted(SNAPSHOT_SOURCE_TYPES)
    ]


def test_account_owner_snapshot_values(ingested_session: Session) -> None:
    snapshot = snapshot_for(ingested_session, scope_for(ingested_session, "USR-5001", "OPP-1001"))

    opportunity = snapshot.opportunity.record
    assert opportunity.acv == Decimal(4217500)
    assert opportunity.close_date == date(2026, 5, 17)
    assert opportunity.stage == "6.0 Order Review"
    assert snapshot.citations()[0] == (
        "source=synthetic_data/salesforce/opportunities.tsv, opportunity_id=OPP-1001"
    )


def test_restricted_owner_sees_both_pending_discounts(ingested_session: Session) -> None:
    snapshot = snapshot_for(ingested_session, scope_for(ingested_session, "USR-5003", "OPP-1003"))

    discounts = [note.record.requested_discount for note in snapshot.pricing_notes]
    assert discounts == [Decimal(18), Decimal(12)]


def assert_reveals_no_pricing(snapshot: DealSnapshot) -> None:
    payload = snapshot.model_dump_json()
    assert snapshot.pricing_visibility == PricingVisibility.NONE
    assert snapshot.pricing_notes == []
    assert not [marker for marker in HIDDEN_PRICING_MARKERS if marker in payload]


def test_narrow_scope_shows_no_pricing_and_no_hint_of_it(ingested_session: Session) -> None:
    assert_reveals_no_pricing(
        snapshot_for(ingested_session, scope_for(ingested_session, "USR-5007", "OPP-1001"))
    )


def test_hidden_sensitive_notes_look_the_same_as_no_notes(ingested_session: Session) -> None:
    scope = scope_for(ingested_session, "USR-5003", "OPP-1003").model_copy(
        update={"sensitive_pricing_allowed": False}
    )

    assert_reveals_no_pricing(snapshot_for(ingested_session, scope))


def test_snapshot_carries_no_contact_details(ingested_session: Session) -> None:
    snapshot = snapshot_for(ingested_session, scope_for(ingested_session, "USR-5005", "OPP-1002"))

    assert "@" not in snapshot.model_dump_json()


def test_snapshot_round_trips(ingested_session: Session) -> None:
    snapshot = snapshot_for(ingested_session, scope_for(ingested_session, "USR-5003", "OPP-1003"))

    assert DealSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def mutated_owner_snapshot(session: Session, **changes: object) -> dict[str, object]:
    snapshot = snapshot_for(session, scope_for(session, "USR-5001", "OPP-1001"))
    return {**snapshot.model_dump(mode="json"), **changes}


def test_visibility_must_match_the_notes(ingested_session: Session) -> None:
    payload = mutated_owner_snapshot(ingested_session, pricing_visibility=PricingVisibility.NONE)

    with pytest.raises(ValidationError, match="pricing_visibility"):
        DealSnapshot.model_validate(payload)


def test_visibility_rejects_partial(ingested_session: Session) -> None:
    with pytest.raises(ValidationError):
        DealSnapshot.model_validate(
            mutated_owner_snapshot(ingested_session, pricing_visibility="partial")
        )


def test_blocks_must_describe_one_deal(ingested_session: Session) -> None:
    other = snapshot_for(ingested_session, scope_for(ingested_session, "USR-5002", "OPP-1002"))
    payload = mutated_owner_snapshot(
        ingested_session, account=other.account.model_dump(mode="json")
    )

    with pytest.raises(ValidationError, match="account does not belong"):
        DealSnapshot.model_validate(payload)


def test_snapshot_forbids_extra_fields(ingested_session: Session) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        DealSnapshot.model_validate(mutated_owner_snapshot(ingested_session, hidden_note_count=0))


def test_snapshot_module_never_imports_the_llm_package() -> None:
    # A fresh interpreter, because this test process may already have imported the LLM package.
    result = subprocess.run(  # noqa: S603  fixed interpreter and code, no user input
        [sys.executable, "-c", IMPORT_PROBE],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )

    assert json.loads(result.stdout) == []
