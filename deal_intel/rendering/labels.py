"""Approval labels: which label an item carries, and its exact wording."""

from collections.abc import Sequence

from deal_intel.contracts.approvals import ApprovalStatus, ApprovalView
from deal_intel.contracts.brief import ApprovalLabel, LabelKind

LABEL_TEXT: dict[LabelKind, str] = {
    LabelKind.PENDING: "[PENDING APPROVAL: {role}] INTERNAL ONLY",
    LabelKind.APPROVED: "[APPROVED by {actor} on {decided_on}]",
    LabelKind.REJECTED: "[REJECTED]",
    LabelKind.ESCALATED: "[ESCALATED: no eligible approver for {role}]",
    LabelKind.EXPIRED: "[APPROVAL EXPIRED]",
    LabelKind.NOT_REQUESTABLE: "[REQUIRES APPROVAL; you are not permitted to request it]",
}
STATUS_LABELS: dict[ApprovalStatus, LabelKind] = {
    ApprovalStatus.PENDING: LabelKind.PENDING,
    ApprovalStatus.APPROVED: LabelKind.APPROVED,
    ApprovalStatus.REJECTED: LabelKind.REJECTED,
    ApprovalStatus.EXPIRED: LabelKind.EXPIRED,
    ApprovalStatus.ESCALATED: LabelKind.ESCALATED,
}
NOT_REQUESTABLE = ApprovalLabel(kind=LabelKind.NOT_REQUESTABLE)


def label_for(view: ApprovalView) -> ApprovalLabel:
    kind = STATUS_LABELS[view.approval.status]
    if kind is LabelKind.APPROVED and view.last_event is not None:
        return ApprovalLabel(
            kind=kind,
            actor_user_id=view.last_event.actor_user_id,
            decided_on=view.last_event.at.date(),
        )
    return ApprovalLabel(kind=kind, role=view.approval.required_role)


def label_text(label: ApprovalLabel) -> str:
    return LABEL_TEXT[label.kind].format(
        role=label.role.value if label.role else "",
        actor=label.actor_user_id or "",
        decided_on=label.decided_on.isoformat() if label.decided_on else "",
    )


def labels_text(labels: Sequence[ApprovalLabel]) -> str:
    return " ".join(label_text(label) for label in labels)


def all_approved(views: Sequence[ApprovalView]) -> bool:
    return bool(views) and all(view.approval.status is ApprovalStatus.APPROVED for view in views)
