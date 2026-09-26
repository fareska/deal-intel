"""Who may decide an approval: the right CRM role, the run's account, and clearance for the
brief's access level. No legal approver exists in the dataset, by design; the engine escalates
rather than invent one."""

from enum import StrEnum

from sqlalchemy import any_, select
from sqlalchemy.orm import Session

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.approvals import ApproverRole
from deal_intel.contracts.reference import UserProfile
from deal_intel.db.models import UserRow
from deal_intel.permissions.gate import max_access_level_for


class CrmRole(StrEnum):
    DEAL_DESK_APPROVER = "Deal Desk Approver"
    SALES_LEADER = "Sales Leader"
    LEGAL_APPROVER = "Legal Approver"


APPROVER_CRM_ROLES: dict[ApproverRole, frozenset[CrmRole]] = {
    ApproverRole.DEAL_DESK: frozenset({CrmRole.DEAL_DESK_APPROVER}),
    ApproverRole.SALES_LEADER: frozenset({CrmRole.SALES_LEADER}),
    ApproverRole.LEGAL: frozenset({CrmRole.LEGAL_APPROVER}),
    ApproverRole.HUMAN_REVIEWER: frozenset({CrmRole.SALES_LEADER, CrmRole.DEAL_DESK_APPROVER}),
}


def is_eligible(
    user: UserProfile, role: ApproverRole, account_id: str, brief_level: AccessLevel
) -> bool:
    return (
        user.role in APPROVER_CRM_ROLES[role]
        and account_id in user.allowed_account_ids
        and max_access_level_for(user) >= brief_level
    )


def eligible_user_ids(
    session: Session, role: ApproverRole, account_id: str, brief_level: AccessLevel
) -> list[str]:
    statement = (
        select(UserRow)
        .where(
            UserRow.role.in_([crm_role.value for crm_role in APPROVER_CRM_ROLES[role]]),
            account_id == any_(UserRow.allowed_account_ids),
        )
        .order_by(UserRow.user_id)
    )
    users = [
        UserProfile.model_validate(row, from_attributes=True) for row in session.scalars(statement)
    ]
    return [user.user_id for user in users if is_eligible(user, role, account_id, brief_level)]
