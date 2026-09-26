from enum import StrEnum
from typing import ClassVar


class LlmErrorCode(StrEnum):
    MODEL_REFUSAL = "MODEL_REFUSAL"
    SCHEMA_VALIDATION = "SCHEMA_VALIDATION"
    OUTPUT_NOT_SALVAGEABLE = "OUTPUT_NOT_SALVAGEABLE"
    TOOL_BUDGET_EXCEEDED = "TOOL_BUDGET_EXCEEDED"
    FIXTURE_MISSING = "FIXTURE_MISSING"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    UNKNOWN_MODEL_PRICE = "UNKNOWN_MODEL_PRICE"


class LlmError(RuntimeError):
    """`error_code` is what spans and stage records store; the message may carry details."""

    error_code: ClassVar[LlmErrorCode]


class ModelRefusal(LlmError):
    error_code = LlmErrorCode.MODEL_REFUSAL


class SchemaValidationError(LlmError):
    error_code = LlmErrorCode.SCHEMA_VALIDATION

    def __init__(self, message: str, raw_text: str | None) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class OutputNotSalvageable(LlmError):
    """Retries are exhausted and dropping the offending items breaks the output contract."""

    error_code = LlmErrorCode.OUTPUT_NOT_SALVAGEABLE


class ToolBudgetExceeded(LlmError):
    error_code = LlmErrorCode.TOOL_BUDGET_EXCEEDED


class FixtureMissing(LlmError):
    error_code = LlmErrorCode.FIXTURE_MISSING


class ProviderError(LlmError):
    error_code = LlmErrorCode.PROVIDER_ERROR


class UnknownModelPrice(LlmError):
    error_code = LlmErrorCode.UNKNOWN_MODEL_PRICE
