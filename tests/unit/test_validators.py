from collections.abc import Callable

import pytest
from pydantic import Field, ValidationError

from deal_intel.contracts.agents.conversation_intelligence import (
    ConversationFindings,
    Finding,
    Urgency,
)
from deal_intel.contracts.agents.negotiation_strategy import (
    NegotiationState,
    StrategyOutput,
    SummarySentence,
)
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import PackChunk
from deal_intel.contracts.guardrails import (
    Confidence,
    EvidenceBacked,
    GuardrailCheck,
    GuardrailOutcome,
)
from deal_intel.contracts.reference import LowMediumHigh
from deal_intel.guardrails.text import FigureKind, extract_figures, extract_quotes
from deal_intel.guardrails.validators import (
    FINDING_CHECKS,
    STAKEHOLDER_CHECKS,
    EvidenceIndex,
    enforce_bounds,
    run_checks,
    validate_citations,
    validate_numbers,
    validate_quotes,
)

type PackChunkFactory = Callable[[str, str], PackChunk]

MAX_CLAIMS = 12
CALL = "gong_summary:CALL-027"
PRICING = "pricing:PN-4004"
CONTACT = "contact:CON-3006"
UNKNOWN_POLICY = "policy:legal-signoff"
CALL_TEXT = (
    "Procurement asked for a 22% discount on the $4,217,500 renewal. Dana Ortiz said "
    '"we cannot sign before the security review closes" and the team agreed to follow up.'
)
PRICING_TEXT = "Approved floor is an 18% discount, valid until 2026-03-31 for OPP-1003."
CONTACT_TEXT = "Dana Ortiz, VP Procurement, contact CON-3006."


class Claim(EvidenceBacked):
    statement: str


class Findings(StrictModel):
    claims: list[Claim] = Field(max_length=MAX_CLAIMS)


class Person(EvidenceBacked):
    name: str
    contact_id: str | None = None


class People(StrictModel):
    people: list[Person]


@pytest.fixture
def evidence(pack_chunk: PackChunkFactory) -> EvidenceIndex:
    return EvidenceIndex.from_chunks(
        [
            pack_chunk(CALL, CALL_TEXT),
            pack_chunk(PRICING, PRICING_TEXT),
            pack_chunk(CONTACT, CONTACT_TEXT),
        ]
    )


def claim(statement: str, *evidence_ids: str) -> Claim:
    return Claim(statement=statement, evidence_ids=list(evidence_ids), confidence=Confidence.HIGH)


def outcomes(results: object) -> set[tuple[GuardrailCheck, GuardrailOutcome]]:
    return {(result.check, result.outcome) for result in results}  # type: ignore[attr-defined]


@pytest.mark.parametrize("stated", ["$4,217,500", "4217500", "$4.2M", "4.2 million", "$4.2m"])
def test_equivalent_amounts_match_the_evidence_figure(stated: str) -> None:
    (source,) = extract_figures("a renewal of $4,217,500")
    (figure,) = extract_figures(f"the deal is {stated} in total")

    assert figure.matches(source)


def test_percentages_do_not_match_amounts() -> None:
    (percent,) = extract_figures("a 22% discount")
    (amount,) = extract_figures("a $22 fee")

    assert percent.kind is FigureKind.PERCENT
    assert not percent.matches(amount)


@pytest.mark.parametrize(
    "text", ["valid until 2026-03-31", "renewal in 2026", "see OPP-1003 and CALL-027", "Q3 plan"]
)
def test_dates_years_and_ids_are_not_figures(text: str) -> None:
    assert extract_figures(text) == ()


def test_grounded_output_passes_every_check_unchanged(evidence: EvidenceIndex) -> None:
    output = Findings(
        claims=[
            claim("Procurement asked for 22% on a $4.2M renewal.", CALL),
            claim('Dana said "we cannot sign before the security review closes".', CALL),
            claim("The approved floor is 18 percent.", PRICING),
        ]
    )

    report = run_checks(output, evidence, FINDING_CHECKS)

    assert report.passed
    assert report.output is output
    assert outcomes(report.results) == {
        (GuardrailCheck.CITATIONS, GuardrailOutcome.PASSED),
        (GuardrailCheck.NUMBERS, GuardrailOutcome.PASSED),
        (GuardrailCheck.QUOTES, GuardrailOutcome.PASSED),
    }


def test_unknown_citation_drops_the_item(evidence: EvidenceIndex) -> None:
    kept = claim("Procurement asked for 22%.", CALL)
    output = Findings(claims=[kept, claim("Legal signed off.", UNKNOWN_POLICY)])

    report = validate_citations(output, evidence)

    assert report.output.claims == [kept]
    assert outcomes(report.results) == {(GuardrailCheck.CITATIONS, GuardrailOutcome.DROPPED)}
    assert UNKNOWN_POLICY in report.feedback[0]
    assert report.feedback[0].startswith("claims[1]:")


def test_figure_absent_from_the_cited_chunk_drops_the_item(evidence: EvidenceIndex) -> None:
    output = Findings(claims=[claim("The approved floor is 22%.", PRICING)])

    report = validate_numbers(output, evidence)

    assert report.output.claims == []
    assert outcomes(report.results) == {(GuardrailCheck.NUMBERS, GuardrailOutcome.DROPPED)}
    assert "22%" in report.feedback[0]


def test_figure_is_checked_against_cited_chunks_only(evidence: EvidenceIndex) -> None:
    output = Findings(claims=[claim("The approved floor is 18%.", CALL)])

    assert validate_numbers(output, evidence).output.claims == []


def test_altered_quote_is_unquoted_and_kept(evidence: EvidenceIndex) -> None:
    output = Findings(
        claims=[claim('Dana said "we cannot sign before the legal review closes".', CALL)]
    )

    report = validate_quotes(output, evidence)

    (kept,) = report.output.claims
    assert kept.statement == "Dana said we cannot sign before the legal review closes."
    assert extract_quotes(kept.statement) == []
    assert outcomes(report.results) == {(GuardrailCheck.QUOTES, GuardrailOutcome.MODIFIED)}


def test_quote_with_different_spacing_still_verifies(evidence: EvidenceIndex) -> None:
    output = Findings(
        claims=[claim('Dana said "we  cannot sign\nbefore the security review closes".', CALL)]
    )

    assert validate_quotes(output, evidence).passed


def test_short_quoted_terms_are_not_treated_as_quotations(evidence: EvidenceIndex) -> None:
    output = Findings(claims=[claim('They want a "proof pack" first.', CALL)])

    assert validate_quotes(output, evidence).passed


def test_invented_person_is_dropped(evidence: EvidenceIndex) -> None:
    real = Person(
        name="Dana Ortiz", contact_id="CON-3006", evidence_ids=[CONTACT], confidence=Confidence.HIGH
    )
    invented = Person(name="Jordan Blake", evidence_ids=[CALL], confidence=Confidence.LOW)
    unknown_contact = Person(
        name="Dana Ortiz", contact_id="CON-9999", evidence_ids=[CALL], confidence=Confidence.LOW
    )

    report = run_checks(
        People(people=[real, invented, unknown_contact]), evidence, STAKEHOLDER_CHECKS
    )

    assert report.output.people == [real]
    assert (GuardrailCheck.NAMES, GuardrailOutcome.DROPPED) in outcomes(report.results)
    assert any("Jordan Blake" in message for message in report.feedback)
    assert any("CON-9999" in message for message in report.feedback)


def test_over_long_list_is_truncated_to_the_schema_bound() -> None:
    items = [
        {"statement": f"claim {n}", "evidence_ids": [CALL], "confidence": "high"} for n in range(15)
    ]
    raw = {"claims": items}

    report = enforce_bounds(raw, Findings)

    assert len(report.output["claims"]) == MAX_CLAIMS  # type: ignore[arg-type]
    assert len(raw["claims"]) == 15
    assert outcomes(report.results) == {(GuardrailCheck.BOUNDS, GuardrailOutcome.WARNING)}
    Findings.model_validate(report.output)


def summary(*texts: str) -> list[SummarySentence]:
    return [
        SummarySentence(text=text, evidence_ids=[CALL], confidence=Confidence.HIGH)
        for text in texts
    ]


def strategy_with_state(state: NegotiationState) -> StrategyOutput:
    return StrategyOutput(
        executive_summary=summary(
            "Procurement wants a discount.", "Security review is open.", "Renewal is at risk."
        ),
        negotiation_state=state,
    )


def negotiation_state(customer_position: str, *evidence_ids: str) -> NegotiationState:
    return NegotiationState(
        stage_assessment="Late-stage renewal.",
        customer_position=customer_position,
        vendor_position="Holding list price for now.",
        evidence_ids=list(evidence_ids),
        confidence=Confidence.MEDIUM,
    )


def test_optional_single_item_is_checked_and_dropped(evidence: EvidenceIndex) -> None:
    kept = Finding(
        statement="Procurement asked for 22%.", evidence_ids=[CALL], confidence=Confidence.HIGH
    )
    ungrounded = Urgency(
        level=LowMediumHigh.HIGH,
        rationale="The customer demands a 30% cut.",
        evidence_ids=[CALL],
        confidence=Confidence.HIGH,
    )
    output = ConversationFindings(objections=[kept], urgency=ungrounded)

    report = run_checks(output, evidence, FINDING_CHECKS)

    assert report.output.urgency is None
    assert report.output.objections == [kept]
    (dropped,) = [r for r in report.results if r.outcome is GuardrailOutcome.DROPPED]
    assert (dropped.check, dropped.item_ref) == (GuardrailCheck.NUMBERS, "urgency")
    assert report.feedback[0].startswith("urgency:")


def test_absent_optional_item_passes(evidence: EvidenceIndex) -> None:
    output = ConversationFindings(urgency=None)

    report = run_checks(output, evidence, FINDING_CHECKS)

    assert report.passed
    assert report.output is output


def test_required_single_item_has_unverified_quotes_unquoted(evidence: EvidenceIndex) -> None:
    output = strategy_with_state(
        negotiation_state('They said "we cannot sign before the legal review closes".', CALL)
    )

    report = run_checks(output, evidence, FINDING_CHECKS)

    state = report.output.negotiation_state
    assert state is not None
    assert state.customer_position == "They said we cannot sign before the legal review closes."
    assert (GuardrailCheck.QUOTES, GuardrailOutcome.MODIFIED) in outcomes(report.results)
    assert report.feedback[0].startswith("negotiation_state:")


def test_dropping_a_required_single_item_breaks_its_contract(evidence: EvidenceIndex) -> None:
    output = strategy_with_state(negotiation_state("Wants legal sign-off.", UNKNOWN_POLICY))

    report = run_checks(output, evidence, FINDING_CHECKS)

    assert report.output.negotiation_state is None
    assert (GuardrailCheck.CITATIONS, GuardrailOutcome.DROPPED) in outcomes(report.results)
    with pytest.raises(ValidationError, match="negotiation_state is required"):
        StrategyOutput.model_validate(report.output.model_dump())


def test_validators_never_mutate_their_input(evidence: EvidenceIndex) -> None:
    output = Findings(
        claims=[
            claim('Dana said "we cannot sign before the legal review closes".', CALL),
            claim("Floor is 22%.", PRICING),
        ]
    )
    snapshot = output.model_dump()

    report = run_checks(output, evidence, FINDING_CHECKS)

    assert output.model_dump() == snapshot
    assert report.output is not output
