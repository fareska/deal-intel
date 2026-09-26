import json
from dataclasses import dataclass
from functools import cache

from pydantic import BaseModel, ValidationError

from deal_intel.contracts.guardrails import GuardrailResult
from deal_intel.guardrails.validators import enforce_bounds
from deal_intel.llm.errors import SchemaValidationError

MAX_REPORTED_ERRORS = 10
ROOT_LOCATION = "output"
NO_TEXT_MESSAGE = "the final turn contained no text output"
NOT_AN_OBJECT_MESSAGE = "the output must be a single JSON object"
SCHEMA_PROPERTIES = "properties"


@dataclass(frozen=True)
class ParsedOutput[OutputT: BaseModel]:
    output: OutputT
    bound_results: tuple[GuardrailResult, ...]


def describe_validation_error(error: ValidationError) -> str:
    """Locations and messages only; the offending input is left out because it can be large."""
    details = error.errors(include_url=False, include_input=False)[:MAX_REPORTED_ERRORS]
    return "; ".join(
        f"{'.'.join(str(part) for part in detail['loc']) or ROOT_LOCATION}: {detail['msg']}"
        for detail in details
    )


def parse_output[OutputT: BaseModel](
    output_model: type[OutputT], text: str | None
) -> ParsedOutput[OutputT]:
    """Truncates over-long lists before validating: truncation resolves a bounds violation on its
    own, so it costs a warning rather than a retry."""
    if text is None:
        raise SchemaValidationError(NO_TEXT_MESSAGE, None)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise SchemaValidationError(f"output is not valid JSON: {error.msg}", text) from error
    if not isinstance(raw, dict):
        raise SchemaValidationError(NOT_AN_OBJECT_MESSAGE, text)
    undeclared = sorted(set(raw) - model_facing_fields(output_model))
    if undeclared:
        raise SchemaValidationError(
            f"output has fields the schema does not declare: {undeclared}", text
        )
    bounded = enforce_bounds(raw, output_model)
    try:
        output = output_model.model_validate(bounded.output)
    except ValidationError as error:
        raise SchemaValidationError(describe_validation_error(error), text) from error
    return ParsedOutput(output=output, bound_results=bounded.results)


@cache
def model_facing_fields(output_model: type[BaseModel]) -> frozenset[str]:
    """The top-level fields of the schema the model is given; harness-only fields are absent."""
    return frozenset(output_model.model_json_schema().get(SCHEMA_PROPERTIES, {}))
