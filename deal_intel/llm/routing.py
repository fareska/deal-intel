from deal_intel.config import Settings
from deal_intel.contracts.llm import ModelRole, ModelRoute


def route_for(role: ModelRole, settings: Settings) -> ModelRoute:
    """Thinking and effort belong to the role, not the request, so no agent can run the
    extraction model with a strategy reasoning budget."""
    if role is ModelRole.STRATEGY:
        return ModelRoute(
            model=settings.model_strategy, adaptive_thinking=True, effort=settings.strategy_effort
        )
    return ModelRoute(model=settings.model_extraction, adaptive_thinking=False, effort=None)
