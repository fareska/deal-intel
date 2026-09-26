"""Calls the real model API. Deselected by default; run with `uv run pytest tests/live -m live`."""

import os

import pytest
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.config import get_settings
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.llm import LlmRequest, ModelRole
from deal_intel.llm.anthropic_client import AnthropicProvider
from deal_intel.llm.client import LlmClient
from deal_intel.observability.tracing import NoopTracer

pytestmark = pytest.mark.live

API_KEY_VARIABLE = "ANTHROPIC_API_KEY"


class CityFacts(StrictModel):
    city: str
    population: int


@pytest.mark.parametrize("role", list(ModelRole))
def test_structured_output_round_trip(
    role: ModelRole, session_factory: sessionmaker[Session]
) -> None:
    settings = get_settings()
    if settings.anthropic_api_key is None and not os.environ.get(API_KEY_VARIABLE):
        pytest.skip(f"{API_KEY_VARIABLE} is not set")
    client = LlmClient(
        AnthropicProvider(settings),
        settings=settings,
        session_factory=session_factory,
        tracer=NoopTracer(),
    )

    result = client.complete(
        LlmRequest[CityFacts](
            agent_name="live_smoke",
            prompt_version="v1",
            prompt_hash="live-smoke",
            model_role=role,
            system="Answer with the requested fields only.",
            user_message="Name the capital of Portugal and its approximate population.",
            output_model=CityFacts,
            max_tokens=2048,
            fresh=True,
        )
    )

    assert isinstance(result.output, CityFacts)
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.cost_usd > 0
