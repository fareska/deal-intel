from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field

from deal_intel.contracts.access import AccessLevel, SourceType
from deal_intel.contracts.base import StrictModel

USER_ID_PATTERN = r"^USR-\d{4}$"
ACCOUNT_ID_PATTERN = r"^ACC-\d{4}$"
OPPORTUNITY_ID_PATTERN = r"^OPP-\d{4}$"
CONTACT_ID_PATTERN = r"^CON-\d{4}$"
PRICING_NOTE_ID_PATTERN = r"^PN-\d{4}$"
CALL_ID_PATTERN = r"^CALL-\d{3}$"
SLACK_UPDATE_ID_PATTERN = r"^SLK-\d{4}-\d{2}$"

ACCOUNT_ACCESS_LEVELS: tuple[AccessLevel, ...] = (AccessLevel.STANDARD, AccessLevel.RESTRICTED)


class LowMediumHigh(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SlackAuthorRole(StrEnum):
    AE = "AE"
    SE = "SE"
    CSM = "CSM"


def split_comma_list(value: object) -> object:
    # Database rows already hold lists, so non-strings pass through for the lookups.
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def require_account_access_level(level: AccessLevel) -> AccessLevel:
    if level not in ACCOUNT_ACCESS_LEVELS:
        raise ValueError(f"account access_level must be one of {list(ACCOUNT_ACCESS_LEVELS)}")
    return level


CommaSeparated = BeforeValidator(split_comma_list)
AccountAccessLevel = Annotated[AccessLevel, AfterValidator(require_account_access_level)]


class UserProfile(StrictModel):
    user_id: str = Field(pattern=USER_ID_PATTERN)
    user_name: str
    role: str
    allowed_account_ids: Annotated[list[str], CommaSeparated]
    allowed_source_types: Annotated[list[SourceType], CommaSeparated]
    can_view_sensitive_pricing: bool
    can_request_approval: bool
    can_view_restricted_account: bool


class Account(StrictModel):
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    account_name: str
    industry: str
    region: str
    country: str
    employee_band: str
    current_products: str
    account_health: str
    strategic_notes: str
    access_level: AccountAccessLevel


class Opportunity(StrictModel):
    opportunity_id: str = Field(pattern=OPPORTUNITY_ID_PATTERN)
    opportunity_name: str
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    account_name: str
    stage: str
    type: str
    region: str
    country: str
    industry: str
    owner: str
    close_date: date
    acv: Decimal
    tcv: Decimal
    renewal_term_months: int = Field(gt=0)
    probability: int = Field(ge=0, le=100)
    forecast_category: str
    next_step: str
    primary_competitor: str
    risk_level: LowMediumHigh
    approval_required: bool
    restricted_access: bool


class Contact(StrictModel):
    contact_id: str = Field(pattern=CONTACT_ID_PATTERN)
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    full_name: str
    title: str
    role_in_deal: str
    email: str
    phone: str
    location: str
    influence_level: LowMediumHigh
    sentiment: str
    last_interaction_date: date
    notes: str


class PricingNote(StrictModel):
    pricing_note_id: str = Field(pattern=PRICING_NOTE_ID_PATTERN)
    opportunity_id: str = Field(pattern=OPPORTUNITY_ID_PATTERN)
    current_acv: Decimal
    proposed_acv: Decimal
    requested_discount: Decimal
    renewal_uplift: Decimal
    commercial_risk: LowMediumHigh
    approval_status: str
    pricing_notes: str


class GongCallSummary(StrictModel):
    call_id: str = Field(pattern=CALL_ID_PATTERN)
    opportunity_id: str = Field(pattern=OPPORTUNITY_ID_PATTERN)
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    call_date: date
    title: str
    duration: str
    stage_at_call: str
    participants: Annotated[list[str], CommaSeparated]
    summary: str
    key_points: str
    customer_sentiment: str
    risks: str
    next_steps: str
    source_access_level: AccessLevel


class SlackUpdate(StrictModel):
    """Field order is the documented column order of the Slack TSV."""

    update_id: str = Field(pattern=SLACK_UPDATE_ID_PATTERN)
    opportunity_id: str = Field(pattern=OPPORTUNITY_ID_PATTERN)
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    update_date: date
    channel: str
    author_role: SlackAuthorRole
    synthetic_notice: str
    source_access_level: AccessLevel
    update_text: str = Field(min_length=1)
