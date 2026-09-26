from http import HTTPStatus

import pytest

from deal_intel.api.request_context import REQUEST_ID_HEADER
from deal_intel.api.schemas import ErrorCode, ErrorResponse
from deal_intel.contracts.access import DENIED_MESSAGE
from deal_intel.contracts.api import BriefFormat, BriefResponse, RunAccepted, RunStatusResponse
from deal_intel.contracts.approvals import ApprovalStatus, Decision
from deal_intel.contracts.brief import SECTION_HEADINGS
from deal_intel.contracts.runs import RunState
from tests.unit.api_harness import (
    ACCOUNT_2003,
    ACCOUNT_BIOMATERIALS,
    ACCOUNT_ECLIPSE,
    DEAL_DESK,
    NARROW_READER,
    OPP_1001,
    OPP_1003,
    OUTSIDER,
    REQUESTER_1001,
    REQUESTER_1003,
    SUPPLIED_REQUEST_ID,
    create_completed_run,
)


def error_of(payload: object) -> ErrorResponse:
    return ErrorResponse.model_validate(payload)


def test_post_runs_returns_202_and_completes(api_client) -> None:
    response = api_client.post(
        "/runs",
        json={"opportunity_id": OPP_1001, "user_id": REQUESTER_1001, "fresh": False},
        headers={REQUEST_ID_HEADER: SUPPLIED_REQUEST_ID},
    )

    assert response.status_code == 202
    assert response.headers[REQUEST_ID_HEADER] == SUPPLIED_REQUEST_ID
    accepted = RunAccepted.model_validate(response.json())
    status = RunStatusResponse.model_validate(
        api_client.get(f"/runs/{accepted.run_id}", params={"user_id": REQUESTER_1001}).json()
    )
    assert status.state is RunState.COMPLETED
    assert not accepted.existing


def test_second_post_without_fresh_returns_the_same_run(api_client) -> None:
    first = create_completed_run(api_client, REQUESTER_1001, OPP_1001)
    second = api_client.post(
        "/runs", json={"opportunity_id": OPP_1001, "user_id": REQUESTER_1001, "fresh": False}
    )

    assert second.status_code == 202
    accepted = RunAccepted.model_validate(second.json())
    assert accepted.run_id == first
    assert accepted.existing


def test_fresh_bypasses_idempotency(api_client) -> None:
    first = create_completed_run(api_client, REQUESTER_1001, OPP_1001)
    second = create_completed_run(api_client, REQUESTER_1001, OPP_1001, fresh=True)

    assert second != first


def test_brief_json_has_nine_headings_and_markdown_matches(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1001, OPP_1001)

    json_body = BriefResponse.model_validate(
        api_client.get(
            f"/runs/{run_id}/brief",
            params={"user_id": REQUESTER_1001, "format": BriefFormat.JSON.value},
        ).json()
    )
    markdown = BriefResponse.model_validate(
        api_client.get(
            f"/runs/{run_id}/brief",
            params={"user_id": REQUESTER_1001, "format": BriefFormat.MARKDOWN.value},
        ).json()
    )

    assert json_body.brief is not None
    assert markdown.markdown is not None
    for heading in SECTION_HEADINGS:
        assert heading in markdown.markdown


def test_unauthorised_brief_reader_gets_the_same_404_as_unknown(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1003, OPP_1003)
    headers = {REQUEST_ID_HEADER: SUPPLIED_REQUEST_ID}

    forbidden = api_client.get(
        f"/runs/{run_id}/brief", params={"user_id": NARROW_READER}, headers=headers
    )
    missing = api_client.get(
        "/runs/does-not-exist/brief", params={"user_id": REQUESTER_1001}, headers=headers
    )

    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()
    assert error_of(forbidden.json()).error.code is ErrorCode.NOT_FOUND
    assert forbidden.headers[REQUEST_ID_HEADER] == SUPPLIED_REQUEST_ID


def test_denied_run_returns_202_then_generic_denial(api_client) -> None:
    response = api_client.post(
        "/runs", json={"opportunity_id": OPP_1003, "user_id": NARROW_READER, "fresh": False}
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    status = api_client.get(f"/runs/{run_id}", params={"user_id": NARROW_READER})
    body = status.text

    assert status.status_code == 200
    parsed = RunStatusResponse.model_validate(status.json())
    assert parsed.state is RunState.DENIED
    assert parsed.message == DENIED_MESSAGE
    for leaked in (ACCOUNT_ECLIPSE, ACCOUNT_BIOMATERIALS, ACCOUNT_2003):
        assert leaked not in body


def test_invalid_opportunity_id_does_not_echo_the_value(api_client) -> None:
    submitted = "NOT-AN-OPP"

    response = api_client.post(
        "/runs", json={"opportunity_id": submitted, "user_id": REQUESTER_1001, "fresh": False}
    )

    assert response.status_code == 422
    error = error_of(response.json()).error
    assert error.code is ErrorCode.INVALID_INPUT
    assert submitted not in response.text


def test_trace_contains_spans_and_no_evidence_text(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1001, OPP_1001)

    response = api_client.get(f"/runs/{run_id}/trace", params={"user_id": REQUESTER_1001})

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["spans"]
    assert "The buyer wants a simple final order form" not in response.text


def test_replay_creates_a_new_version(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1001, OPP_1001)

    replayed = api_client.post(f"/runs/{run_id}/replay", params={"user_id": REQUESTER_1001})

    assert replayed.status_code == 200
    body = BriefResponse.model_validate(replayed.json())
    assert body.version == 2


def test_replay_on_denied_is_conflict(api_client) -> None:
    run_id = create_completed_run(api_client, NARROW_READER, OPP_1003)

    response = api_client.post(f"/runs/{run_id}/replay", params={"user_id": NARROW_READER})

    assert response.status_code == 409
    assert error_of(response.json()).error.code is ErrorCode.CONFLICT


def test_approvals_list_and_decide(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1003, OPP_1003)

    listed = api_client.get(
        "/approvals", params={"user_id": DEAL_DESK, "status": ApprovalStatus.PENDING.value}
    )
    empty = api_client.get(
        "/approvals", params={"user_id": OUTSIDER, "status": ApprovalStatus.PENDING.value}
    )

    assert listed.status_code == 200
    items = listed.json()
    assert items
    assert all(item["run_id"] == run_id for item in items)
    assert empty.json() == []

    approval_id = items[0]["approval_id"]
    forbidden = api_client.post(
        f"/approvals/{approval_id}/decision",
        json={"user_id": OUTSIDER, "decision": Decision.APPROVED.value, "note": "no"},
    )
    assert forbidden.status_code == 403

    for item in items:
        decided = api_client.post(
            f"/approvals/{item['approval_id']}/decision",
            json={"user_id": DEAL_DESK, "decision": Decision.APPROVED.value, "note": "ok"},
        )
        assert decided.status_code == 200

    status = RunStatusResponse.model_validate(
        api_client.get(f"/runs/{run_id}", params={"user_id": REQUESTER_1003}).json()
    )
    assert status.state is RunState.COMPLETED
    assert status.pending_approvals == 0


def test_second_reader_gets_not_found_for_a_brief_outside_their_scope(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1001, OPP_1001)

    response = api_client.get(f"/runs/{run_id}/brief", params={"user_id": NARROW_READER})

    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("action", ["replay", "resume"])
def test_only_the_requester_may_replay_or_resume(api_client, action: str) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1003, OPP_1003)
    assert api_client.get(f"/runs/{run_id}", params={"user_id": DEAL_DESK}).status_code == 200

    response = api_client.post(f"/runs/{run_id}/{action}", params={"user_id": DEAL_DESK})

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_denied_trace_responses_do_not_distinguish_unknown_from_forbidden(api_client) -> None:
    unknown = create_completed_run(api_client, NARROW_READER, "OPP-9999")
    forbidden = create_completed_run(api_client, NARROW_READER, OPP_1003)

    def attribute_keys(run_id: str) -> list[list[str]]:
        body = api_client.get(f"/runs/{run_id}/trace", params={"user_id": NARROW_READER}).json()
        return sorted(sorted(span["attributes"]) for span in body["spans"])

    assert attribute_keys(unknown) == attribute_keys(forbidden)


def test_resume_of_a_completed_run_conflicts(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1001, OPP_1001)

    response = api_client.post(f"/runs/{run_id}/resume", params={"user_id": REQUESTER_1001})

    assert response.status_code == 409
