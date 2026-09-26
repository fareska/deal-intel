from pathlib import Path

from deal_intel.contracts.base import StrictModel

REPO_ROOT = Path(__file__).resolve().parents[2]

FIXTURES_NOT_RECORDED = (
    "agent fixtures are not recorded yet; record fixtures with scripts/record_fixtures.py"
)
FIXTURES_STALE = "recorded fixtures are stale; re-record with scripts/record_fixtures.py"
LIVE_LLM_TESTS_ENV = "LIVE_LLM_TESTS"
LIVE_LLM_TESTS_REQUIRED = "set LIVE_LLM_TESTS=1 to run live model tests"
SLACK_GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "slack_golden.json"
EXPECTED_RULES_PATH = REPO_ROOT / "tests" / "fixtures" / "expected_rules.json"
GOLDEN_ROOT = REPO_ROOT / "tests" / "fixtures" / "golden"
EVAL_BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "eval_baseline.json"
INJECTION_ROOT = REPO_ROOT / "tests" / "fixtures" / "injection"

USR_5001 = "USR-5001"
USR_5002 = "USR-5002"
USR_5003 = "USR-5003"
USR_5004 = "USR-5004"
USR_5007 = "USR-5007"
OPP_1001 = "OPP-1001"
OPP_1002 = "OPP-1002"
OPP_1003 = "OPP-1003"


class DemoPair(StrictModel):
    user_id: str
    opportunity_id: str

    @property
    def label(self) -> str:
        return f"{self.user_id}/{self.opportunity_id}"

    @property
    def golden_name(self) -> str:
        return f"{self.user_id}_{self.opportunity_id}.json"


# The three authorised demo scenarios whose agent calls are recorded as fixtures.
RECORDED_PAIRS: tuple[DemoPair, ...] = (
    DemoPair(user_id=USR_5001, opportunity_id=OPP_1001),
    DemoPair(user_id=USR_5002, opportunity_id=OPP_1002),
    DemoPair(user_id=USR_5003, opportunity_id=OPP_1003),
)

DENIED_PAIR = DemoPair(user_id=USR_5007, opportunity_id=OPP_1003)

# Authorised demos plus the required denial; used by evaluation goldens and metrics.
EVAL_PAIRS: tuple[DemoPair, ...] = (*RECORDED_PAIRS, DENIED_PAIR)

# Leakage canaries: two denials, one narrow reader, one full reader.
LEAKAGE_PAIRS: tuple[DemoPair, ...] = (
    DemoPair(user_id=USR_5007, opportunity_id=OPP_1003),
    DemoPair(user_id=USR_5004, opportunity_id=OPP_1003),
    DemoPair(user_id=USR_5007, opportunity_id=OPP_1001),
    DemoPair(user_id=USR_5001, opportunity_id=OPP_1001),
)
