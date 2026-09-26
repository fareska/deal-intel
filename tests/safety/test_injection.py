"""Prompt-injection fixtures against each agent, including through the tool loop.

Hand-authored fake-client turns prove the assertions and the poisoned-chunk path. Live
duplicates (deselected by default) run the same checks against the real model.
"""

import os

import pytest

from deal_intel.agents.base import AgentRuntime, run_agent
from deal_intel.config import LlmClientKind, get_settings
from deal_intel.contracts.agents.common import AgentName
from deal_intel.evaluation.injection import (
    InjectionFixture,
    persist_injection_chunk,
    with_injected_chunk,
)
from deal_intel.evaluation.scenarios import LIVE_LLM_TESTS_ENV
from deal_intel.llm.factory import build_llm_client
from deal_intel.observability.tracing import NoopTracer
from tests.safety.support import (
    INJECTION_OPPORTUNITY,
    INJECTION_USER,
    SPECS,
    assert_resisted_injection,
    injection_cases,
    run_injected_agent,
)
from tests.unit.test_agents import AgentBench

API_KEY_VARIABLE = "ANTHROPIC_API_KEY"
INJECTION_CASES = injection_cases()


@pytest.mark.parametrize(
    ("agent_name", "fixture"),
    [(name, fixture) for name, fixture, _ in INJECTION_CASES],
    ids=[label for _, _, label in INJECTION_CASES],
)
def test_each_agent_resists_each_injection(
    bench: AgentBench, agent_name: AgentName, fixture: InjectionFixture
) -> None:
    run = run_injected_agent(bench, agent_name, fixture)

    assert_resisted_injection(run.output, fixture)
    if SPECS[agent_name].tools:
        assert fixture.chunk.chunk_id in run.tool_evidence_ids()


@pytest.mark.live
@pytest.mark.parametrize(
    ("agent_name", "fixture"),
    [(name, fixture) for name, fixture, _ in INJECTION_CASES],
    ids=[f"live-{label}" for _, _, label in INJECTION_CASES],
)
def test_each_agent_resists_each_injection_live(
    bench: AgentBench, agent_name: AgentName, fixture: InjectionFixture
) -> None:
    settings = get_settings()
    if settings.anthropic_api_key is None and not os.environ.get(API_KEY_VARIABLE):
        pytest.skip(f"{API_KEY_VARIABLE} is not set")
    if os.environ.get(LIVE_LLM_TESTS_ENV) != "1":
        pytest.skip(f"set {LIVE_LLM_TESTS_ENV}=1 to run live model tests")
    persist_injection_chunk(bench.session, fixture.chunk)
    base = bench.runtime(INJECTION_USER, INJECTION_OPPORTUNITY)
    spec = SPECS[agent_name]
    pack_build = with_injected_chunk(bench.pack(spec, base), fixture.chunk)
    runtime = AgentRuntime(
        retriever=base.retriever,
        llm=build_llm_client(
            settings.model_copy(update={"llm_client": LlmClientKind.ANTHROPIC}),
            bench.session_factory,
            NoopTracer(),
        ),
        tracer=bench.tracer,
        settings=settings,
        fresh=True,
    )
    if agent_name is AgentName.NEGOTIATION_STRATEGY:
        run = bench.run_strategy(runtime, pack_build, None, None)
    else:
        run = run_agent(spec, runtime, pack_build=pack_build)
    assert_resisted_injection(run.output, fixture)
