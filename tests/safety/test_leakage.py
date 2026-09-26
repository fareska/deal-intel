"""Zero canary hits across briefs, API bodies, UI pages, denial payloads, and spans."""

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from deal_intel.api.templating import UI_PATH_PREFIX
from deal_intel.contracts.access import Denied
from deal_intel.contracts.brief import Brief
from deal_intel.evaluation.scenarios import LEAKAGE_PAIRS, DemoPair
from deal_intel.guardrails.canaries import (
    CanarySet,
    build_canary_set,
    denial_leaks,
    find_leaks,
    run_span_texts,
)
from deal_intel.orchestration.persistence import get_run
from deal_intel.permissions.gate import authorize
from deal_intel.rendering.brief import latest_brief
from deal_intel.retrieval.ingest import latest_snapshot_id
from tests.unit.api_harness import create_completed_run


@dataclass(frozen=True)
class LeakageSurface:
    pair: DemoPair
    run_id: str
    texts: tuple[str, ...]
    canaries: CanarySet


@pytest.fixture
def leakage_surfaces(api_client: TestClient, committed_session: Session) -> list[LeakageSurface]:
    return [collect_surface(api_client, committed_session, pair) for pair in LEAKAGE_PAIRS]


def collect_surface(client: TestClient, session: Session, pair: DemoPair) -> LeakageSurface:
    run_id = create_completed_run(client, pair.user_id, pair.opportunity_id)
    texts = [
        client.get(f"/runs/{run_id}", params={"user_id": pair.user_id}).text,
        client.get(f"/runs/{run_id}/brief", params={"user_id": pair.user_id}).text,
        client.get(f"{UI_PATH_PREFIX}/runs/{run_id}", params={"user_id": pair.user_id}).text,
        *run_span_texts(session, run_id),
    ]
    row = latest_brief(session, run_id)
    if row is not None:
        brief = Brief.model_validate(row.json)
        texts += [row.markdown, brief.model_dump_json()]
    access = authorize(session, pair.user_id, pair.opportunity_id)
    known = [pair.user_id, pair.opportunity_id]
    if isinstance(access, Denied):
        leaks = denial_leaks(session, run_id, known, texts)
        snapshot_id = latest_snapshot_id(session)
        assert snapshot_id is not None
        canaries = build_canary_set(session, snapshot_id, None, known)
        assert not leaks
        return LeakageSurface(pair, run_id, tuple(texts), canaries)
    snapshot_id = get_run(session, run_id).snapshot_id or latest_snapshot_id(session)
    assert snapshot_id is not None
    canaries = build_canary_set(session, snapshot_id, access.scope, known)
    return LeakageSurface(pair, run_id, tuple(texts), canaries)


@pytest.mark.parametrize("pair", LEAKAGE_PAIRS, ids=lambda pair: pair.label)
def test_no_canary_hits_on_any_surface(
    leakage_surfaces: list[LeakageSurface], pair: DemoPair
) -> None:
    surface = next(item for item in leakage_surfaces if item.pair == pair)

    assert surface.canaries.size() > 0
    assert find_leaks(surface.texts, surface.canaries) == []
