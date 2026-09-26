from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from deal_intel.api.dependencies import get_db_session
from deal_intel.api.main import create_app
from deal_intel.api.request_context import REQUEST_ID_HEADER
from deal_intel.api.schemas import ErrorBody, ErrorCode
from deal_intel.api.services.users import list_users
from deal_intel.api.templating import ERROR_TEMPLATE, templates
from deal_intel.retrieval.reference import ReferenceData, load_reference_data

SCRIPT_PAYLOAD = "<script>alert(1)</script>"
ESCAPED_PAYLOAD = "&lt;script&gt;alert(1)&lt;/script&gt;"
SUPPLIED_REQUEST_ID = "ui-request-0001"
VIEWER = "USR-5003"
NOT_FOUND_TEXT = "does not exist, or you do not have access to it"


def build_probe_app() -> FastAPI:
    app = create_app()

    @app.get("/ui/probe/escape", response_class=HTMLResponse)
    def render_payload(request: Request) -> HTMLResponse:
        error = ErrorBody(
            code=ErrorCode.INVALID_INPUT, message=SCRIPT_PAYLOAD, request_id=SUPPLIED_REQUEST_ID
        )
        return templates.TemplateResponse(request, ERROR_TEMPLATE, {"error": error})

    @app.get("/ui/probe/boom")
    def boom() -> None:
        raise RuntimeError("internal detail")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_probe_app())


@pytest.fixture
def reference_session(db_session: Session, dataset_root: Path) -> Session:
    load_reference_data(db_session, dataset_root)
    return db_session


@pytest.fixture
def db_client(reference_session: Session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: reference_session
    yield TestClient(app)


def test_templates_escape_a_script_payload(client: TestClient) -> None:
    response = client.get("/ui/probe/escape")

    assert ESCAPED_PAYLOAD in response.text
    assert SCRIPT_PAYLOAD not in response.text


def test_pages_load_scripts_only_from_static(client: TestClient) -> None:
    html = client.get("/ui/probe/escape").text

    assert '<script src="/static/htmx.min.js" defer></script>' in html
    assert html.count("<script") == 1


def test_unknown_ui_path_renders_the_not_found_page(client: TestClient) -> None:
    response = client.get("/ui/runs/unknown", headers={REQUEST_ID_HEADER: SUPPLIED_REQUEST_ID})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert NOT_FOUND_TEXT in response.text
    assert SUPPLIED_REQUEST_ID in response.text
    assert "Viewing as" in response.text


def test_not_found_page_keeps_the_viewer_in_links(client: TestClient) -> None:
    response = client.get("/ui/runs/unknown", params={"user_id": VIEWER})

    assert f"<strong>{VIEWER}</strong>" in response.text
    assert f'href="/ui?user_id={VIEWER}"' in response.text


def test_malformed_viewer_is_never_echoed(client: TestClient) -> None:
    response = client.get("/ui/runs/unknown", params={"user_id": SCRIPT_PAYLOAD})

    assert SCRIPT_PAYLOAD not in response.text
    assert ESCAPED_PAYLOAD not in response.text
    assert "no user selected" in response.text


def test_unhandled_ui_error_renders_the_error_page(client: TestClient) -> None:
    response = client.get("/ui/probe/boom")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert ErrorCode.INTERNAL_ERROR.value in response.text
    assert "internal detail" not in response.text
    assert "Traceback" not in response.text


def test_static_stylesheet_is_served(client: TestClient) -> None:
    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_list_users_returns_every_user_in_id_order(
    reference_session: Session, reference: ReferenceData
) -> None:
    users = list_users(reference_session)

    assert [user.user_id for user in users] == sorted(reference.users)


def test_landing_page_renders_the_viewer_selector(
    db_client: TestClient, reference: ReferenceData
) -> None:
    response = db_client.get("/ui", params={"user_id": VIEWER})

    assert response.status_code == 200
    assert '<select id="viewer-select" name="user_id">' in response.text
    for user_id in reference.users:
        assert f'<option value="{user_id}"' in response.text
    assert f'<option value="{VIEWER}" selected>' in response.text


def test_landing_page_without_a_viewer_asks_for_one(db_client: TestClient) -> None:
    response = db_client.get("/ui")

    assert '<option value="" selected>Choose a user</option>' in response.text
