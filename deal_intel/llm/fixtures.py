"""Fixture files: `<root>/<agent_name>/<input_hash>.json`, each a JSON list of recorded turns.

A turn's `output` is the parsed JSON when the model returned valid JSON and the raw text
otherwise, so hand-written fixtures stay readable and malformed outputs can still be replayed.
"""

import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter

from deal_intel.contracts.llm import ProviderTurn, RecordedTurn
from deal_intel.llm.errors import FixtureMissing

FIXTURE_SUFFIX = ".json"
RECORD_HINT = "record it with RECORD_FIXTURES=1 and LLM_CLIENT=anthropic"
RECORDED_TURNS = TypeAdapter(list[RecordedTurn])


def fixture_path(root: Path, agent_name: str, input_hash: str) -> Path:
    return root / agent_name / f"{input_hash}{FIXTURE_SUFFIX}"


def load_turns(root: Path, agent_name: str, input_hash: str) -> list[RecordedTurn]:
    path = fixture_path(root, agent_name, input_hash)
    if not path.is_file():
        raise FixtureMissing(f"no fixture at {path}; {RECORD_HINT}")
    return RECORDED_TURNS.validate_json(path.read_bytes())


def write_turns(
    root: Path, agent_name: str, input_hash: str, turns: Sequence[RecordedTurn]
) -> Path:
    path = fixture_path(root, agent_name, input_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(RECORDED_TURNS.dump_json(list(turns), indent=2) + b"\n")
    return path


def to_recorded(turn: ProviderTurn) -> RecordedTurn:
    return RecordedTurn(
        model=turn.model,
        stop_reason=turn.stop_reason,
        usage=turn.usage,
        tool_calls=turn.tool_calls,
        output=json_or_text(turn.output_text),
    )


def to_provider_turn(recorded: RecordedTurn) -> ProviderTurn:
    output = recorded.output
    return ProviderTurn(
        model=recorded.model,
        stop_reason=recorded.stop_reason,
        usage=recorded.usage,
        tool_calls=recorded.tool_calls,
        output_text=output if output is None or isinstance(output, str) else json.dumps(output),
    )


def json_or_text(text: str | None) -> object:
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return parsed if isinstance(parsed, dict | list) else text
