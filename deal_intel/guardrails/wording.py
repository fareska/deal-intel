"""Wording a model output may not use, whatever the evidence says. Pure text matching.

Approval outcomes exist only once a human decides, after the strategy step, so any sentence
asserting one is wrong by construction. Customer-facing text must not reveal internal workflow.
"""

import re
from collections.abc import Sequence

APPROVAL_ASSERTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:is|are|was|were|has been|have been|had been|been) approved\b",
        r"\bapproved status\b",
        r"\bdeal desk (?:has |have )?(?:agreed|okayed|signed off|approved)\b",
        r"\bapproval (?:has been |was )?(?:granted|given|secured|obtained)\b",
    )
)
INTERNAL_WORKFLOW_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bdeal desk\b",
        r"\bsales leader\b",
        r"\bthresholds?\b",
        r"\bpricing notes?\b",
        r"\bPN-\d+\b",
        r"\bapproval status\b",
        r"\b(?:pending|internal) approvals?\b",
        r"\binternal[- ]only\b",
        r"\brestricted\b",
        r"\bslack\b",
        r"\bpolicy rules?\b",
        r"\bdiscounts?\b",
        r"\bconcessions?\b",
    )
)


# Concession phrasing in customer-facing text is allowed only once its approval is granted.
CONCESSION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\boffer(?:s|ed|ing)?\b",
        r"\breduc(?:e|es|ed|ing|tion|tions)\b",
        r"\bwaiv(?:e|es|ed|ing|er)\b",
        r"\bagree(?:s|d)? to\b",
        r"\bguarantee(?:s|d)?\b",
        r"\bdiscount of\b",
        r"\bwe can provide\b",
    )
)


def matched_phrases(patterns: Sequence[re.Pattern[str]], text: str) -> list[str]:
    return [match[0] for pattern in patterns for match in pattern.finditer(text)]


def approval_assertions(text: str) -> list[str]:
    return matched_phrases(APPROVAL_ASSERTION_PATTERNS, text)


def internal_workflow_terms(text: str) -> list[str]:
    return matched_phrases(INTERNAL_WORKFLOW_PATTERNS, text)


def concession_phrases(text: str) -> list[str]:
    return matched_phrases(CONCESSION_PATTERNS, text)
