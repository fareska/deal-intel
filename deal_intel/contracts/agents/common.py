"""Pieces shared by every agent output contract: agent names and text bounds.

Output items extend `contracts.guardrails.EvidenceBacked`, which is what the validators look for.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from deal_intel.contracts.access import SourceType
from deal_intel.contracts.evidence import CHUNK_SOURCE_TYPES, chunk_kind_of
from deal_intel.contracts.guardrails import EvidenceBacked

MAX_LIST_ITEMS = 12
MAX_LABEL_CHARS = 120
MAX_NOTE_CHARS = 300
MAX_STATEMENT_CHARS = 400


class AgentName(StrEnum):
    """Also the prompt folder, the pack `agent_name`, and the fixture folder of each agent."""

    DEAL_SNAPSHOT = "deal_snapshot"
    CONVERSATION_INTELLIGENCE = "conversation_intelligence"
    STAKEHOLDER_MAP = "stakeholder_map"
    NEGOTIATION_STRATEGY = "negotiation_strategy"


SUBAGENTS: tuple[AgentName, ...] = (
    AgentName.CONVERSATION_INTELLIGENCE,
    AgentName.STAKEHOLDER_MAP,
)
LLM_AGENTS: tuple[AgentName, ...] = (*SUBAGENTS, AgentName.NEGOTIATION_STRATEGY)

Label = Annotated[str, Field(min_length=1, max_length=MAX_LABEL_CHARS)]
Note = Annotated[str, Field(min_length=1, max_length=MAX_NOTE_CHARS)]
Statement = Annotated[str, Field(min_length=1, max_length=MAX_STATEMENT_CHARS)]
Notes = Annotated[list[Note], Field(max_length=MAX_LIST_ITEMS)]

# Set only by the harness: the flag is left out of the schema the model sees, and the parse path
# rejects any output key that schema does not declare, so a model output can never carry it.
HarnessFlag = SkipJsonSchema[bool]


def source_types_of(item: EvidenceBacked) -> frozenset[SourceType]:
    """Derived from the cited ids rather than asked of the model, so it cannot disagree."""
    return frozenset(CHUNK_SOURCE_TYPES[chunk_kind_of(chunk_id)] for chunk_id in item.evidence_ids)
