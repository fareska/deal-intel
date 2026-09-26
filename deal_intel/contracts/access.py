from enum import StrEnum

from pydantic import field_serializer

from deal_intel.contracts.base import StrictModel

DENIED_MESSAGE = "You are not authorized to generate a brief for this request."


class SourceType(StrEnum):
    SALESFORCE = "salesforce"
    GONG = "gong"
    SLACK = "slack"
    PRICING = "pricing"
    POLICIES = "policies"


class AccessLevel(StrEnum):
    """Declaration order is the access order: standard < restricted < sensitive_pricing."""

    STANDARD = "standard"
    RESTRICTED = "restricted"
    SENSITIVE_PRICING = "sensitive_pricing"

    @property
    def rank(self) -> int:
        return list(AccessLevel).index(self)

    # str already orders alphabetically (restricted < sensitive_pricing < standard), so all
    # four comparisons are overridden; functools.total_ordering would keep the str versions.
    def __lt__(self, other: object) -> bool:
        return self.rank < rank_of(other)

    def __le__(self, other: object) -> bool:
        return self.rank <= rank_of(other)

    def __gt__(self, other: object) -> bool:
        return self.rank > rank_of(other)

    def __ge__(self, other: object) -> bool:
        return self.rank >= rank_of(other)


def rank_of(value: object) -> int:
    # Returning NotImplemented would let str's alphabetical comparison answer instead.
    if not isinstance(value, AccessLevel):
        raise TypeError(f"cannot order an AccessLevel against {type(value).__name__}")
    return value.rank


class DenialReason(StrEnum):
    UNKNOWN_USER = "UNKNOWN_USER"
    UNKNOWN_OPPORTUNITY = "UNKNOWN_OPPORTUNITY"
    ACCOUNT_NOT_ALLOWED = "ACCOUNT_NOT_ALLOWED"
    RESTRICTED_ACCOUNT = "RESTRICTED_ACCOUNT"


class AccessScope(StrictModel):
    user_id: str
    role: str
    account_id: str
    opportunity_id: str
    source_types: frozenset[SourceType]
    max_access_level: AccessLevel
    pricing_allowed: bool
    sensitive_pricing_allowed: bool
    policies_allowed: bool
    can_request_approval: bool

    @field_serializer("source_types")
    def serialize_source_types(self, source_types: frozenset[SourceType]) -> list[str]:
        return sorted(source_types)


class Allowed(StrictModel):
    scope: AccessScope


class Denied(StrictModel):
    """Carries no field that could hold account data, so a serialised denial cannot leak it."""

    reason_code: DenialReason
    user_id: str
    opportunity_id: str

    @property
    def message(self) -> str:
        return DENIED_MESSAGE
