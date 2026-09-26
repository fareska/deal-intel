from deal_intel.contracts.base import StrictModel

FIXTURES_NOT_RECORDED = (
    "agent fixtures are not recorded yet; record fixtures with scripts/record_fixtures.py"
)
FIXTURES_STALE = "recorded fixtures are stale; re-record with scripts/record_fixtures.py"


class DemoPair(StrictModel):
    user_id: str
    opportunity_id: str

    @property
    def label(self) -> str:
        return f"{self.user_id}/{self.opportunity_id}"


# The three authorised demo scenarios whose agent calls are recorded as fixtures.
RECORDED_PAIRS: tuple[DemoPair, ...] = (
    DemoPair(user_id="USR-5001", opportunity_id="OPP-1001"),
    DemoPair(user_id="USR-5002", opportunity_id="OPP-1002"),
    DemoPair(user_id="USR-5003", opportunity_id="OPP-1003"),
)
