"""A broken access-level filter is caught by the Python scope assertion before any model call."""

import pytest

from deal_intel.agents.base import AgentRuntime, run_agent
from deal_intel.agents.conversation_intelligence import SPEC
from deal_intel.agents.scope_guard import ScopeViolation, out_of_scope_ids
from deal_intel.contracts.access import AccessLevel, Allowed
from deal_intel.evaluation.scenarios import OPP_1003, USR_5003
from deal_intel.permissions.gate import authorize
from deal_intel.retrieval.retriever import ScopedRetriever
from tests.safety.support import ACCESS_LEVEL_PREDICATE_INDEX
from tests.unit.test_agents import AgentBench


def test_dropped_level_filter_raises_before_any_model_call(bench: AgentBench, monkeypatch) -> None:
    access = authorize(bench.session, USR_5003, OPP_1003)
    assert isinstance(access, Allowed)
    scope = access.scope.model_copy(
        update={
            "max_access_level": AccessLevel.STANDARD,
            "sensitive_pricing_allowed": False,
        }
    )
    original = ScopedRetriever._scope_predicates

    def without_level_filter(retriever: ScopedRetriever, requested):
        predicates = original(retriever, requested)
        return [
            predicate
            for index, predicate in enumerate(predicates)
            if index != ACCESS_LEVEL_PREDICATE_INDEX
        ]

    monkeypatch.setattr(ScopedRetriever, "_scope_predicates", without_level_filter)
    retriever = ScopedRetriever(bench.session, scope)
    base = bench.runtime(USR_5003, OPP_1003)
    runtime = AgentRuntime(
        retriever=retriever,
        llm=base.llm,
        tracer=base.tracer,
        settings=base.settings,
    )
    pack_build = bench.pack(SPEC, runtime)
    leaked = out_of_scope_ids(scope, pack_build.pack.chunks, SPEC.source_types)
    assert leaked

    with pytest.raises(ScopeViolation) as raised:
        run_agent(SPEC, runtime, pack_build=pack_build)

    assert raised.value.chunk_ids
    assert bench.provider.requests == []
