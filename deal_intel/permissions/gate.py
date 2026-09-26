import re

from sqlalchemy.orm import Session

from deal_intel.contracts.access import (
    AccessLevel,
    AccessScope,
    Allowed,
    DenialReason,
    Denied,
    SourceType,
)
from deal_intel.contracts.reference import (
    OPPORTUNITY_ID_PATTERN,
    USER_ID_PATTERN,
    Account,
    Opportunity,
    UserProfile,
)
from deal_intel.permissions.lookups import find_account, find_opportunity, find_user

USER_ID_REGEX = re.compile(USER_ID_PATTERN)
OPPORTUNITY_ID_REGEX = re.compile(OPPORTUNITY_ID_PATTERN)


class InvalidInput(ValueError):
    pass


def authorize(session: Session, user_id: str, opportunity_id: str) -> Allowed | Denied:
    validate_identifiers(user_id, opportunity_id)
    user = find_user(session, user_id)
    if user is None:
        return deny(DenialReason.UNKNOWN_USER, user_id, opportunity_id)
    opportunity = find_opportunity(session, opportunity_id)
    account = None if opportunity is None else find_account(session, opportunity.account_id)
    if opportunity is None or account is None:
        return deny(DenialReason.UNKNOWN_OPPORTUNITY, user_id, opportunity_id)
    return decide(user, opportunity, account)


def validate_identifiers(user_id: object, opportunity_id: object) -> None:
    if not matches(USER_ID_REGEX, user_id) or not matches(OPPORTUNITY_ID_REGEX, opportunity_id):
        raise InvalidInput("user_id must match USR-dddd and opportunity_id must match OPP-dddd")


def matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def decide(user: UserProfile, opportunity: Opportunity, account: Account) -> Allowed | Denied:
    """Pure: no database, clock, or I/O. Membership is checked first so a user outside the
    account never learns that the account is also restricted."""
    if account.account_id not in user.allowed_account_ids:
        return deny(DenialReason.ACCOUNT_NOT_ALLOWED, user.user_id, opportunity.opportunity_id)
    if is_restricted(opportunity, account) and not user.can_view_restricted_account:
        return deny(DenialReason.RESTRICTED_ACCOUNT, user.user_id, opportunity.opportunity_id)
    return Allowed(scope=build_scope(user, opportunity, account))


def deny(reason: DenialReason, user_id: str, opportunity_id: str) -> Denied:
    return Denied(reason_code=reason, user_id=user_id, opportunity_id=opportunity_id)


def is_restricted(opportunity: Opportunity, account: Account) -> bool:
    return account.access_level == AccessLevel.RESTRICTED or opportunity.restricted_access


def baseline_access_level(opportunity: Opportunity, account: Account) -> AccessLevel:
    """The lowest level any evidence about this opportunity may carry."""
    return AccessLevel.RESTRICTED if is_restricted(opportunity, account) else AccessLevel.STANDARD


def build_scope(user: UserProfile, opportunity: Opportunity, account: Account) -> AccessScope:
    source_types = frozenset(user.allowed_source_types)
    pricing_allowed = SourceType.PRICING in source_types
    return AccessScope(
        user_id=user.user_id,
        role=user.role,
        account_id=account.account_id,
        opportunity_id=opportunity.opportunity_id,
        source_types=source_types,
        max_access_level=max_access_level_for(user),
        pricing_allowed=pricing_allowed,
        sensitive_pricing_allowed=pricing_allowed and user.can_view_sensitive_pricing,
        policies_allowed=SourceType.POLICIES in source_types,
        can_request_approval=user.can_request_approval,
    )


def max_access_level_for(user: UserProfile) -> AccessLevel:
    if user.can_view_sensitive_pricing:
        return AccessLevel.SENSITIVE_PRICING
    if user.can_view_restricted_account:
        return AccessLevel.RESTRICTED
    return AccessLevel.STANDARD
