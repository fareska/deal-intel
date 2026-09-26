from typing import Annotated

from fastapi import APIRouter, Query

from deal_intel.api.dependencies import DbSession, ReaderId, Runtime
from deal_intel.api.services import approvals as approval_services
from deal_intel.contracts.api import APPROVALS_PATH, ApprovalResponse, DecisionRequest
from deal_intel.contracts.approvals import ApprovalStatus

router = APIRouter()
StatusParam = Annotated[ApprovalStatus | None, Query()]


@router.get(APPROVALS_PATH)
def list_approvals(
    user_id: ReaderId, session: DbSession, runtime: Runtime, status: StatusParam = None
) -> list[ApprovalResponse]:
    return approval_services.approvals_for(session, user_id, status, runtime.clock())


@router.post(f"{APPROVALS_PATH}/{{approval_id}}/decision")
def decide_approval(
    approval_id: str, body: DecisionRequest, session: DbSession, runtime: Runtime
) -> ApprovalResponse:
    return approval_services.decide_approval(session, approval_id, body, runtime.clock())
