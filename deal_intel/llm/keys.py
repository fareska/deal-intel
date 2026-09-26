from pydantic import BaseModel

from deal_intel.contracts.llm import LlmRequest, ModelRoute
from deal_intel.retrieval.hashing import json_sha256, sha256_hex

CACHE_KEY_HEX_CHARS = 32
CACHE_KEY_SEPARATOR = "|"


def input_hash[OutputT: BaseModel](request: LlmRequest[OutputT]) -> str:
    """Everything the model sees except the model itself, so a changed prompt, evidence set,
    tool, or output contract never reuses an old fixture or cached output."""
    return json_sha256(
        {
            "system": request.system,
            "user_message": request.user_message,
            "output_schema": request.output_model.model_json_schema(),
            "tools": [tool.spec().model_dump() for tool in request.tools],
        }
    )


def cache_key(agent_name: str, prompt_hash: str, route: ModelRoute, request_hash: str) -> str:
    """Includes the effort setting with the model, so a changed reasoning budget is a miss."""
    parts = [agent_name, prompt_hash, route.model, route.effort or "", request_hash]
    return sha256_hex(CACHE_KEY_SEPARATOR.join(parts).encode("utf-8"))[:CACHE_KEY_HEX_CHARS]
