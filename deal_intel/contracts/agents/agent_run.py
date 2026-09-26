from collections.abc import Sequence

from pydantic import BaseModel

from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import EvidencePack, RetrievalRecord
from deal_intel.contracts.guardrails import GuardrailResult
from deal_intel.contracts.llm import LlmResult


class AgentRun[OutputT: BaseModel](StrictModel):
    """One agent's finished work. `output` is final, including anything the harness changed
    after the model call; `llm_result` is the call's own account (usage, cost, attempts) and is
    None when the harness answered without a model call."""

    agent_name: AgentName
    prompt_version: str
    prompt_hash: str
    input_hash: str | None
    output: OutputT
    pack: EvidencePack
    retrieval_records: list[RetrievalRecord]
    guardrail_results: list[GuardrailResult]
    llm_result: LlmResult[OutputT] | None

    def amended(self, output: OutputT, results: Sequence[GuardrailResult]) -> "AgentRun[OutputT]":
        """A change the harness makes after the model call, recorded with its own results."""
        return self.model_copy(
            update={"output": output, "guardrail_results": [*self.guardrail_results, *results]}
        )

    def tool_evidence_ids(self) -> list[str]:
        return self.llm_result.tool_evidence_ids if self.llm_result else []

    def citable_ids(self) -> frozenset[str]:
        """The pack plus every chunk a tool returned during the call."""
        return self.pack.chunk_ids() | frozenset(self.tool_evidence_ids())
