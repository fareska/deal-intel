"""Policy and approval contracts: rules, requests, decisions, and the policy stage output."""

from datetime import datetime
from enum import StrEnum

from pydantic import JsonValue

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.runs import RunState

SUBJECT_SEPARATOR = ":"


class ApproverRole(StrEnum):
    DEAL_DESK = "deal_desk"
    SALES_LEADER = "sales_leader"
    LEGAL = "legal"
    HUMAN_REVIEWER = "human_reviewer"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    # No eligible approver exists; the run does not wait on it (plan change C8).
    ESCALATED = "escalated"


class Decision(StrEnum):
    APPROVED = ApprovalStatus.APPROVED.value
    REJECTED = ApprovalStatus.REJECTED.value


APPROVAL_EVENT_OUTCOMES: tuple[ApprovalStatus, ...] = (
    ApprovalStatus.APPROVED,
    ApprovalStatus.REJECTED,
    ApprovalStatus.EXPIRED,
)


class RuleId(StrEnum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    R5 = "R5"
    R6 = "R6"
    R7 = "R7"


class RuleEffect(StrEnum):
    APPROVAL = "approval"
    APPROVAL_NO_CUSTOMER_LANGUAGE = "approval_no_customer_language"
    APPROVAL_EXTERNAL_LANGUAGE_WITHHELD = "approval_external_language_withheld"
    SUPPRESS_UNTIL_APPROVED = "suppress_until_approved"
    REVIEW = "review"


class SubjectKind(StrEnum):
    ACTION = "action"
    PRICING = "pricing"


def subject_id(kind: SubjectKind, key: str) -> str:
    return f"{kind.value}{SUBJECT_SEPARATOR}{key}"


class ApprovalRequest(StrictModel):
    """One (subject, role) group of fired rules (plan change C12). `recommendation_ids` lists
    every recommendation the decision covers: a pricing note and the actions sharing it."""

    subject_id: str
    recommendation_ids: list[str]
    required_role: ApproverRole
    rule_ids: list[RuleId]
    proposed_values: dict[str, JsonValue]
    summary: str
    evidence_ids: list[str]


class ApprovalRecord(StrictModel):
    """Field names equal the `approvals` columns."""

    approval_id: str
    run_id: str
    subject_id: str
    recommendation_ids: list[str]
    rule_ids: list[RuleId]
    required_role: ApproverRole
    account_id: str
    access_level: AccessLevel
    eligible_user_ids: list[str]
    status: ApprovalStatus
    proposed_values: dict[str, JsonValue]
    summary: str
    evidence_ids: list[str]
    created_at: datetime
    expires_at: datetime


class ApprovalEvent(StrictModel):
    """Field names equal the `approval_events` columns; `actor_user_id` is None for expiry."""

    approval_id: str
    outcome: ApprovalStatus
    actor_user_id: str | None
    note: str
    at: datetime


class ApprovalView(StrictModel):
    """An approval with its latest event, which is what the renderer labels."""

    approval: ApprovalRecord
    last_event: ApprovalEvent | None


class DecisionResult(StrictModel):
    approval: ApprovalRecord
    pending_remaining: int
    run_state: RunState
    brief_version: int


class PolicyOutput(StrictModel):
    """`requests` are the fired groups; rows exist for them only when `requestable`. The
    renderer labels unrequestable groups instead (policy section 11.3)."""

    requests: list[ApprovalRequest]
    requestable: bool
    approval_ids: list[str]
    fired_rules: dict[str, list[RuleId]]
    brief_access_level: AccessLevel
