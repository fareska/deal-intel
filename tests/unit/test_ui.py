from fastapi.testclient import TestClient

from deal_intel.api.main import create_app
from deal_intel.api.middleware import SECURITY_HEADERS
from deal_intel.api.runtime import build_runtime
from deal_intel.api.templating import UI_PATH_PREFIX, polls_run_status
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.approvals import ApprovalStatus, Decision
from deal_intel.contracts.brief import SECTION_HEADINGS
from deal_intel.contracts.runs import RunState
from tests.unit.api_harness import (
    DEAL_DESK,
    NARROW_READER,
    OPP_1001,
    OPP_1003,
    OUTSIDER,
    REQUESTER_1001,
    REQUESTER_1003,
    create_completed_run,
)
from tests.unit.conftest import DispatchingAgents
from tests.unit.test_ui_base import ESCAPED_PAYLOAD, NOT_FOUND_TEXT, SCRIPT_PAYLOAD

HTMX_HEADER = {"HX-Request": "true"}
ACCOUNT_2003 = "ACC-2003"


def test_polls_run_status_only_while_working() -> None:
    assert polls_run_status(RunState.ANALYZING)
    assert polls_run_status(RunState.QUEUED)
    assert not polls_run_status(RunState.FAILED)
    assert not polls_run_status(RunState.DENIED)
    assert not polls_run_status(RunState.COMPLETED)
    assert not polls_run_status(RunState.AWAITING_APPROVAL)
    assert not polls_run_status("FAILED")


def test_new_run_page_has_selectors_and_fresh_checkbox(api_client) -> None:
    response = api_client.get(f"{UI_PATH_PREFIX}/runs/new", params={"user_id": REQUESTER_1001})

    assert response.status_code == 200
    assert 'name="opportunity_id"' in response.text
    assert 'name="user_id"' in response.text
    assert 'name="fresh"' in response.text
    assert "Fresh run (live model calls)" in response.text


def test_brief_page_renders_nine_sections_and_citations(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1001, OPP_1001)

    response = api_client.get(f"{UI_PATH_PREFIX}/runs/{run_id}", params={"user_id": REQUESTER_1001})

    assert response.status_code == 200
    for heading in SECTION_HEADINGS:
        assert heading in response.text
    assert "OPP-1001" in response.text
    assert "Confidence and Review Warnings" in response.text
    assert "hx-trigger" not in response.text
    assert "Trace" in response.text


def test_narrow_reader_sees_the_generic_not_found_page(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1003, OPP_1003)

    response = api_client.get(f"{UI_PATH_PREFIX}/runs/{run_id}", params={"user_id": NARROW_READER})

    assert response.status_code == 404
    assert NOT_FOUND_TEXT in response.text
    assert "Deal Snapshot" not in response.text
    assert "Recommended Next Actions" not in response.text


def test_brief_page_escapes_a_script_payload(run_bench) -> None:
    agents = run_bench.agents(OPP_1001)
    mapped = agents.outputs[AgentName.STAKEHOLDER_MAP]
    first = mapped.stakeholders[0]
    agents.outputs[AgentName.STAKEHOLDER_MAP] = mapped.model_copy(
        update={
            "stakeholders": [
                first.model_copy(update={"stance_summary": SCRIPT_PAYLOAD}),
                *mapped.stakeholders[1:],
            ]
        }
    )
    runtime = build_runtime(
        settings=run_bench.settings,
        session_factory=run_bench.session_factory,
        agents=DispatchingAgents({OPP_1001: agents, OPP_1003: run_bench.agents(OPP_1003)}),
        llm=run_bench.llm,
        tracer=run_bench.tracer,
        clock=run_bench.clock,
        inline=True,
    )
    runtime.start()
    try:
        with TestClient(create_app(runtime)) as client:
            run_id = create_completed_run(client, REQUESTER_1001, OPP_1001)
            page = client.get(f"{UI_PATH_PREFIX}/runs/{run_id}", params={"user_id": REQUESTER_1001})
    finally:
        runtime.shutdown()

    assert ESCAPED_PAYLOAD in page.text
    assert SCRIPT_PAYLOAD not in page.text


def test_csp_header_on_brief_page(api_client) -> None:
    run_id = create_completed_run(api_client, REQUESTER_1001, OPP_1001)

    response = api_client.get(f"{UI_PATH_PREFIX}/runs/{run_id}", params={"user_id": REQUESTER_1001})

    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_approvals_htmx_row_swap(api_client) -> None:
    create_completed_run(api_client, REQUESTER_1003, OPP_1003)
    listed = api_client.get(f"{UI_PATH_PREFIX}/approvals", params={"user_id": DEAL_DESK})
    outsider = api_client.get(f"{UI_PATH_PREFIX}/approvals", params={"user_id": OUTSIDER})

    assert listed.status_code == 200
    assert "deal_desk" in listed.text
    assert ACCOUNT_2003 not in outsider.text

    approval_id = pending_approval_id(api_client)
    response = api_client.post(
        f"{UI_PATH_PREFIX}/approvals/{approval_id}/decision",
        data={"user_id": DEAL_DESK, "decision": Decision.APPROVED.value, "note": "ok"},
        headers=HTMX_HEADER,
    )

    assert response.status_code == 200
    assert "approved" in response.text
    assert "<html" not in response.text.lower()


def test_approvals_no_js_form_redirects(api_client) -> None:
    create_completed_run(api_client, REQUESTER_1003, OPP_1003, fresh=True)
    approval_id = pending_approval_id(api_client)

    response = api_client.post(
        f"{UI_PATH_PREFIX}/approvals/{approval_id}/decision",
        data={"user_id": DEAL_DESK, "decision": Decision.REJECTED.value, "note": "no"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"{UI_PATH_PREFIX}/approvals")


def test_denied_ui_run_shows_status_not_a_brief(api_client) -> None:
    run_id = create_completed_run(api_client, NARROW_READER, OPP_1003)

    response = api_client.get(f"{UI_PATH_PREFIX}/runs/{run_id}", params={"user_id": NARROW_READER})

    assert response.status_code == 200
    assert "DENIED" in response.text
    assert "Deal Snapshot" not in response.text
    assert "hx-trigger" not in response.text
    assert "Eclipse" not in response.text
    assert "Trace" in response.text


def pending_approval_id(client) -> str:
    items = client.get(
        "/approvals", params={"user_id": DEAL_DESK, "status": ApprovalStatus.PENDING.value}
    ).json()
    assert items
    return items[0]["approval_id"]
