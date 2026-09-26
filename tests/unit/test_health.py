from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from deal_intel.api.dependencies import get_db_session
from deal_intel.api.main import create_app


class UnreachableDatabaseSession:
    def execute(self, *_args: object, **_kwargs: object) -> None:
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))


def client_with_session(session: object) -> TestClient:
    app: FastAPI = create_app()
    app.dependency_overrides[get_db_session] = lambda: session
    return TestClient(app)


@pytest.fixture
def db_client(db_session: Session) -> Iterator[TestClient]:
    yield client_with_session(db_session)


def test_healthz_returns_ok() -> None:
    response = TestClient(create_app()).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_503_when_database_is_unreachable() -> None:
    response = client_with_session(UnreachableDatabaseSession()).get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "database_unavailable"}


def test_readyz_returns_ok_when_database_answers(db_client: TestClient) -> None:
    response = db_client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
