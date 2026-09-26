from collections.abc import Mapping
from decimal import Decimal

from deal_intel.contracts.llm import LlmUsage, ModelPrice
from deal_intel.db.models.llm import COST_SCALE
from deal_intel.llm.errors import UnknownModelPrice

TOKENS_PER_PRICE_UNIT = Decimal(1_000_000)
COST_QUANTUM = Decimal(1).scaleb(-COST_SCALE)


def cost_usd(prices: Mapping[str, ModelPrice], model: str, usage: LlmUsage) -> Decimal:
    """Raises for a model without a price rather than silently costing it at zero."""
    price = prices.get(model)
    if price is None:
        raise UnknownModelPrice(f"no price configured for model {model!r}")
    total = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    )
    return (total / TOKENS_PER_PRICE_UNIT).quantize(COST_QUANTUM)
