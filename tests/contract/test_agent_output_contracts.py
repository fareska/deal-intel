from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from deal_intel.contracts.access import SourceType
from deal_intel.contracts.agents.common import (
    MAX_LIST_ITEMS,
    MAX_STATEMENT_CHARS,
    source_types_of,
)
from deal_intel.contracts.agents.conversation_intelligence import (
    ActionItem,
    ActionSide,
    Conflict,
    ConversationFindings,
    Finding,
    Urgency,
)
from deal_intel.contracts.agents.negotiation_strategy import (
    MAX_NEXT_ACTIONS,
    MAX_SUMMARY_SENTENCES,
    MIN_SUMMARY_SENTENCES,
    NegotiationState,
    NextAction,
    ProposedValues,
    SensitivityTag,
    StrategyOutput,
    SummarySentence,
)
from deal_intel.contracts.agents.stakeholder_map import (
    MAX_STAKEHOLDERS,
    Influence,
    OffCrmPerson,
    RoleInDeal,
    Stakeholder,
    StakeholderMap,
    UnmatchedSpeaker,
)
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.guardrails import MAX_EVIDENCE_IDS, Confidence, EvidenceBacked
from deal_intel.contracts.reference import LowMediumHigh

CALL = "gong_summary:CALL-018"
TRANSCRIPT = "transcript:CALL-018:2"
SLACK = "slack:SLK-1002-02"
CONTACT = "contact:CON-3006"


def cited(*ids: str) -> dict[str, object]:
    return {"evidence_ids": list(ids or (CALL,)), "confidence": Confidence.HIGH}


def finding(statement: str = "Finance approves only a staged payment.") -> Finding:
    return Finding(statement=statement, **cited())


def sample_findings() -> ConversationFindings:
    return ConversationFindings(
        buyer_goals=[finding("Expand to the remaining factories with a boring first rollout.")],
        objections=[finding()],
        urgency=Urgency(level=LowMediumHigh.HIGH, rationale="One packet by early May.", **cited()),
        action_items=[
            ActionItem(
                description="Send the export retest date.",
                side=ActionSide.VENDOR,
                owner="Vendor AE",
                due_date=date(2026, 5, 8),
                **cited(),
            )
        ],
        conflicts=[
            Conflict(
                topic="Proof closeout pack",
                claim_a="The AE considers the closeout item closed.",
                claim_b="The export retest and incident owner map are still required.",
                assessment="The item cannot be closed while its requirements are open.",
                **cited(SLACK, CALL),
            )
        ],
        missing=["No evidence on the legal review timeline."],
        review_notes=["Check whether the closeout pack was received."],
    )


def stakeholder(name: str = "Julian Maro", contact_id: str | None = "CON-3006") -> Stakeholder:
    return Stakeholder(
        name=name,
        title="CIO",
        role_in_deal=RoleInDeal.ECONOMIC_BUYER,
        influence=Influence.HIGH,
        sentiment="cautiously positive",
        stance_summary="Wants the expansion but not rushed.",
        contact_id=contact_id,
        **cited(CONTACT, CALL),
    )


def sample_stakeholders() -> StakeholderMap:
    return StakeholderMap(
        stakeholders=[stakeholder()],
        unmatched_speakers=[UnmatchedSpeaker(name="Niko Hart", **cited(TRANSCRIPT))],
        off_crm_people=[
            OffCrmPerson(
                description="Site IT lead for the first cutover factory",
                role_in_deal=RoleInDeal.TECHNICAL_DECISION_MAKER,
                stance_summary="Owns plant readiness sign-off and wants on-site support.",
                **cited("slack:SLK-1002-01"),
            )
        ],
        missing=["No legal contact identified."],
    )


def sentence(text: str = "The proof passed with two remediation items open.") -> SummarySentence:
    return SummarySentence(text=text, **cited())


def action(
    action_id: str = "A1",
    tags: tuple[SensitivityTag, ...] = (),
    customer_facing: bool = False,
    proposed_values: ProposedValues | None = None,
) -> NextAction:
    return NextAction(
        id=action_id,
        action="Send the proof closeout pack with the retest date.",
        owner_role="Account Executive",
        rationale="Finance ties payment to acceptance of the closeout pack.",
        sensitivity_tags=list(tags),
        customer_facing=customer_facing,
        proposed_values=proposed_values,
        **cited(),
    )


def negotiation_state() -> NegotiationState:
    return NegotiationState(
        stage_assessment="Proof closeout.",
        customer_position="Staged payment with capped enablement.",
        vendor_position="Bounded enablement package.",
        open_items=["Export retest"],
        **cited(),
    )


def sample_strategy(sentence_count: int = MIN_SUMMARY_SENTENCES) -> StrategyOutput:
    pricing = action(
        "A2",
        (SensitivityTag.PRICING, SensitivityTag.DISCOUNT),
        proposed_values=ProposedValues(discount_pct=Decimal(12), uplift_pct=Decimal(-3)),
    )
    return StrategyOutput(
        executive_summary=[sentence() for _ in range(sentence_count)],
        negotiation_state=negotiation_state(),
        next_actions=[action("A1", customer_facing=True), pricing],
        missing_information=["No legal review date."],
        review_warnings=["SLK-1002-02 conflicts with CALL-018."],
    )


SAMPLES: dict[str, StrictModel] = {
    "findings": sample_findings(),
    "findings_empty": ConversationFindings.empty(),
    "stakeholders": sample_stakeholders(),
    "stakeholders_empty": StakeholderMap.empty(),
    "strategy": sample_strategy(),
    "strategy_empty": StrategyOutput.empty(),
}


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_outputs_round_trip_through_json(name: str) -> None:
    model = SAMPLES[name]

    assert type(model).model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_outputs_forbid_extra_fields(name: str) -> None:
    model = SAMPLES[name]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(model).model_validate({**model.model_dump(mode="json"), "unexpected": True})


def test_nested_items_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Finding.model_validate({"statement": "x", **cited(), "source_types": ["gong"]})


@pytest.mark.parametrize(
    "empty",
    [ConversationFindings.empty(), StakeholderMap.empty(), StrategyOutput.empty()],
    ids=["findings", "stakeholders", "strategy"],
)
def test_empty_outputs_flag_no_evidence_and_say_why(empty: StrictModel) -> None:
    dumped = empty.model_dump()

    assert dumped["no_evidence"] is True
    assert any(value for key, value in dumped.items() if key.startswith("missing"))


@pytest.mark.parametrize(
    "output_model", [ConversationFindings, StakeholderMap, StrategyOutput], ids=lambda m: m.__name__
)
def test_output_schemas_carry_list_bounds(output_model: type[StrictModel]) -> None:
    schema = output_model.model_json_schema()

    assert schema["additionalProperties"] is False
    assert "maxItems" in str(schema)


class TestEvidenceIds:
    def test_at_least_one_id_is_required(self) -> None:
        with pytest.raises(ValidationError):
            Finding(statement="x", evidence_ids=[], confidence=Confidence.LOW)

    def test_ids_are_bounded(self) -> None:
        ids = [f"slack:SLK-1001-{number:02d}" for number in range(MAX_EVIDENCE_IDS + 1)]

        with pytest.raises(ValidationError):
            Finding(statement="x", evidence_ids=ids, confidence=Confidence.LOW)

    @pytest.mark.parametrize("bad_id", ["CALL-018", "gong summary:CALL-018", "slack:SLK 1"])
    def test_ids_must_be_chunk_ids(self, bad_id: str) -> None:
        with pytest.raises(ValidationError):
            Finding(statement="x", evidence_ids=[bad_id], confidence=Confidence.LOW)

    def test_source_types_derive_from_ids(self) -> None:
        item = Finding(statement="x", **cited(SLACK, TRANSCRIPT))

        assert source_types_of(item) == {SourceType.SLACK, SourceType.GONG}

    @pytest.mark.parametrize(
        "item_type", [Finding, Urgency, ActionItem, Conflict, Stakeholder, UnmatchedSpeaker]
    )
    def test_items_are_what_the_validators_look_for(self, item_type: type) -> None:
        assert issubclass(item_type, EvidenceBacked)


class TestConversationFindings:
    def test_lists_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            ConversationFindings(objections=[finding()] * (MAX_LIST_ITEMS + 1))

    def test_statements_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            finding("x" * (MAX_STATEMENT_CHARS + 1))

    def test_conflict_needs_both_sides_cited(self) -> None:
        with pytest.raises(ValidationError):
            Conflict(topic="t", claim_a="a", claim_b="b", assessment="c", **cited(SLACK))

    def test_conflict_cannot_repeat_one_id_to_look_two_sided(self) -> None:
        with pytest.raises(ValidationError, match="must not repeat"):
            Conflict(topic="t", claim_a="a", claim_b="b", assessment="c", **cited(SLACK, SLACK))

    def test_urgency_is_optional(self) -> None:
        assert ConversationFindings(objections=[finding()]).urgency is None


class TestStakeholderMap:
    def test_role_outside_the_vocabulary_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Stakeholder.model_validate({**stakeholder().model_dump(), "role_in_deal": "cfo"})

    @pytest.mark.parametrize("field", ["name", "title"])
    def test_vendor_side_people_are_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError, match="vendor-side"):
            Stakeholder.model_validate({**stakeholder().model_dump(), field: "Vendor AE"})

    def test_vendor_speaker_is_not_an_unmatched_speaker(self) -> None:
        with pytest.raises(ValidationError, match="vendor-side"):
            UnmatchedSpeaker(name="Vendor Customer Success", **cited(TRANSCRIPT))

    def test_stakeholders_are_bounded(self) -> None:
        people = [stakeholder(f"Person {number}", None) for number in range(MAX_STAKEHOLDERS + 1)]

        with pytest.raises(ValidationError):
            StakeholderMap(stakeholders=people)

    def test_a_contact_appears_once(self) -> None:
        with pytest.raises(ValidationError, match="one stakeholder only"):
            StakeholderMap(stakeholders=[stakeholder(), stakeholder("Julian M.")])

    def test_contact_id_pattern(self) -> None:
        with pytest.raises(ValidationError):
            stakeholder(contact_id="3006")

    def test_off_crm_person_needs_no_name(self) -> None:
        assert sample_stakeholders().off_crm_people[0].name is None

    def test_roles_missing_counts_off_crm_people(self) -> None:
        assert sample_stakeholders().roles_missing() == [
            RoleInDeal.CHAMPION,
            RoleInDeal.COMMERCIAL_APPROVER,
            RoleInDeal.LEGAL,
        ]


class TestStrategyOutput:
    @pytest.mark.parametrize(
        "sentence_count", [MIN_SUMMARY_SENTENCES - 1, MAX_SUMMARY_SENTENCES + 1]
    )
    def test_summary_has_three_to_six_sentences(self, sentence_count: int) -> None:
        with pytest.raises(ValidationError):
            sample_strategy(sentence_count)

    def test_summary_sentences_are_cited(self) -> None:
        with pytest.raises(ValidationError):
            SummarySentence(text="Uncited.", evidence_ids=[], confidence=Confidence.HIGH)

    def test_negotiation_state_is_required(self) -> None:
        with pytest.raises(ValidationError, match="negotiation_state"):
            StrategyOutput(executive_summary=[sentence()] * 3, negotiation_state=None)

    def test_next_actions_are_bounded(self) -> None:
        actions = [action(f"A{number}") for number in range(1, MAX_NEXT_ACTIONS + 2)]

        with pytest.raises(ValidationError):
            StrategyOutput(
                executive_summary=[sentence()] * 3,
                negotiation_state=negotiation_state(),
                next_actions=actions,
            )

    def test_action_ids_are_unique(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            StrategyOutput(
                executive_summary=[sentence()] * 3,
                negotiation_state=negotiation_state(),
                next_actions=[action("A1"), action("A1")],
            )

    @pytest.mark.parametrize("action_id", ["B1", "A100", "a1"])
    def test_action_id_pattern(self, action_id: str) -> None:
        with pytest.raises(ValidationError):
            action(action_id)

    @pytest.mark.parametrize(
        "values",
        [{"discount_pct": 120}, {"discount_pct": -1}, {"uplift_pct": -101}, {"term_months": 0}],
    )
    def test_proposed_values_are_bounded(self, values: dict[str, int]) -> None:
        with pytest.raises(ValidationError):
            ProposedValues.model_validate(values)

    def test_proposed_values_need_a_value(self) -> None:
        with pytest.raises(ValidationError, match="at least one value"):
            ProposedValues()

    @pytest.mark.parametrize("tag", [SensitivityTag.PRICING, SensitivityTag.DISCOUNT])
    def test_tagged_pricing_action_cannot_be_customer_facing(self, tag: SensitivityTag) -> None:
        with pytest.raises(ValidationError, match="customer_facing = false"):
            action(tags=(tag,), customer_facing=True)

    def test_untagged_discount_cannot_be_customer_facing(self) -> None:
        with pytest.raises(ValidationError, match="customer_facing = false"):
            action(customer_facing=True, proposed_values=ProposedValues(discount_pct=Decimal(18)))

    def test_term_change_alone_may_be_customer_facing(self) -> None:
        assert action(customer_facing=True, proposed_values=ProposedValues(term_months=24))

    def test_tags_must_not_repeat(self) -> None:
        with pytest.raises(ValidationError, match="must not repeat"):
            action(tags=(SensitivityTag.LEGAL_TERMS, SensitivityTag.LEGAL_TERMS))

    def test_no_evidence_output_carries_no_summary(self) -> None:
        with pytest.raises(ValidationError, match="no-evidence"):
            StrategyOutput(
                executive_summary=[sentence()] * 3,
                negotiation_state=negotiation_state(),
                no_evidence=True,
            )
