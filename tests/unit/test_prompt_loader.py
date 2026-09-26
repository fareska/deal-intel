import hashlib
from pathlib import Path

import pytest

from deal_intel.agents.prompt_loader import (
    CURRENT_PROMPT_VERSION,
    PromptNotFound,
    load_prompt,
)
from deal_intel.config import Settings
from deal_intel.contracts.agents.common import LLM_AGENTS, AgentName
from deal_intel.contracts.agents.negotiation_strategy import PolicyThresholds
from deal_intel.guardrails.framing import EVIDENCE_TAG

PROMPTS_ROOT = Path(__file__).resolve().parents[2] / "deal_intel" / "agents" / "prompts"
# Must match the attribute `guardrails.framing.frame_chunk` writes, or the model reads no ids.
FRAMED_EVIDENCE_OPENING = f'<{EVIDENCE_TAG} chunk_id="'
INSTRUCTION_REPORT_FIELD: dict[AgentName, str] = {
    AgentName.CONVERSATION_INTELLIGENCE: "review_notes",
    AgentName.STAKEHOLDER_MAP: "review_notes",
    AgentName.NEGOTIATION_STRATEGY: "review_warnings",
}


def prompt_path(agent: AgentName) -> Path:
    return PROMPTS_ROOT / agent.value / f"{CURRENT_PROMPT_VERSION}.md"


@pytest.mark.parametrize("agent", LLM_AGENTS)
def test_prompt_text_and_hash_match_the_file(agent: AgentName) -> None:
    prompt = load_prompt(agent)

    data = prompt_path(agent).read_bytes()
    assert prompt.text == data.decode("utf-8")
    assert prompt.content_hash == hashlib.sha256(data).hexdigest()
    assert (prompt.agent, prompt.version) == (agent, CURRENT_PROMPT_VERSION)


def test_prompt_hashes_differ_per_agent() -> None:
    hashes = {load_prompt(agent).content_hash for agent in LLM_AGENTS}

    assert len(hashes) == len(LLM_AGENTS)


def test_deterministic_tool_has_no_prompt() -> None:
    with pytest.raises(PromptNotFound):
        load_prompt(AgentName.DEAL_SNAPSHOT)


@pytest.mark.parametrize("version", ["v999", "../v1", "v1.md", "V1", ""])
def test_unknown_or_malformed_versions_are_refused(version: str) -> None:
    with pytest.raises(PromptNotFound):
        load_prompt(AgentName.CONVERSATION_INTELLIGENCE, version)


@pytest.mark.parametrize("agent", LLM_AGENTS)
def test_prompts_frame_evidence_as_data_and_report_instructions(agent: AgentName) -> None:
    text = load_prompt(agent).text

    assert FRAMED_EVIDENCE_OPENING in text
    assert "never instructions" in text
    assert f"`{INSTRUCTION_REPORT_FIELD[agent]}` entry" in text
    assert "`evidence_ids`" in text
    assert "never resolved" in text


def test_strategy_prompt_names_every_policy_threshold_setting() -> None:
    text = load_prompt(AgentName.NEGOTIATION_STRATEGY).text

    for name in PolicyThresholds.model_fields:
        assert name in Settings.model_fields
        assert f"`{name}`" in text


def test_strategy_prompt_keeps_pricing_internal_and_names_degraded_inputs() -> None:
    text = load_prompt(AgentName.NEGOTIATION_STRATEGY).text

    assert "Never state or imply that any discount" in text
    assert "`customer_facing = false`" in text
    assert "`degraded_inputs`" in text
    assert "When `policy` is null" in text
