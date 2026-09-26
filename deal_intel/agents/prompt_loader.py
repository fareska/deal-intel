"""Versioned prompt files, read from the package so they also load from an installed wheel.

A prompt's hash goes on stage outputs and spans, which is how a prompt edit becomes visible in
traces and invalidates cached outputs.
"""

import re
from functools import lru_cache
from importlib.resources import files

from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.base import StrictModel
from deal_intel.retrieval.hashing import sha256_hex

PROMPTS_PACKAGE = "deal_intel.agents"
PROMPTS_DIRECTORY = "prompts"
PROMPT_SUFFIX = ".md"
PROMPT_ENCODING = "utf-8"
CURRENT_PROMPT_VERSION = "v1"
PROMPT_VERSION_REGEX = re.compile(r"v\d+")


class PromptNotFound(LookupError):
    pass


class Prompt(StrictModel):
    agent: AgentName
    version: str
    text: str
    content_hash: str


@lru_cache
def load_prompt(agent: AgentName, version: str = CURRENT_PROMPT_VERSION) -> Prompt:
    # The version becomes part of a path, so anything but vN is refused before touching disk.
    if PROMPT_VERSION_REGEX.fullmatch(version) is None:
        raise PromptNotFound(f"prompt version must look like v1, got {version!r}")
    resource = (
        files(PROMPTS_PACKAGE) / PROMPTS_DIRECTORY / agent.value / f"{version}{PROMPT_SUFFIX}"
    )
    if not resource.is_file():
        raise PromptNotFound(f"no prompt {version} for {agent.value}")
    data = resource.read_bytes()
    return Prompt(
        agent=agent,
        version=version,
        text=data.decode(PROMPT_ENCODING),
        content_hash=sha256_hex(data),
    )
