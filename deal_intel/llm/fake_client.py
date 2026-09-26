from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from deal_intel.contracts.llm import LlmUsage, ProviderRequest, ProviderTurn, StopReason
from deal_intel.llm.errors import FixtureMissing
from deal_intel.llm.fixtures import fixture_path, load_turns, to_provider_turn


class FakeLlmProvider:
    """Replays recorded turns. The turn to serve is the number of model turns already in the
    conversation, so tool loops and feedback retries replay deterministically without state."""

    def __init__(self, fixtures_root: Path, fail_agents: Iterable[str] = ()) -> None:
        self._fixtures_root = fixtures_root
        self._fail_agents = frozenset(fail_agents)
        self.calls: Counter[str] = Counter()

    def send(self, request: ProviderRequest) -> ProviderTurn:
        self.calls[request.agent_name] += 1
        if request.agent_name in self._fail_agents:
            return refusal_turn(request.route.model)
        turns = load_turns(self._fixtures_root, request.agent_name, request.input_hash)
        index = completed_turns(request)
        if index >= len(turns):
            path = fixture_path(self._fixtures_root, request.agent_name, request.input_hash)
            raise FixtureMissing(f"{path} has {len(turns)} turns; turn {index} was requested")
        return to_provider_turn(turns[index])


def completed_turns(request: ProviderRequest) -> int:
    return sum(isinstance(entry, ProviderTurn) for entry in request.conversation)


def refusal_turn(model: str) -> ProviderTurn:
    return ProviderTurn(model=model, stop_reason=StopReason.REFUSAL, usage=LlmUsage())
