from fastapi.testclient import TestClient

REQUESTER_1001 = "USR-5001"
REQUESTER_1003 = "USR-5003"
DEAL_DESK = "USR-5005"
OUTSIDER = "USR-5004"
NARROW_READER = "USR-5007"
OPP_1001 = "OPP-1001"
OPP_1003 = "OPP-1003"
SUPPLIED_REQUEST_ID = "api-request-0001"
ACCOUNT_ECLIPSE = "Eclipse"
ACCOUNT_BIOMATERIALS = "BioMaterials"
ACCOUNT_2003 = "ACC-2003"


def create_completed_run(
    client: TestClient, user_id: str, opportunity_id: str, fresh: bool = False
) -> str:
    response = client.post(
        "/runs", json={"opportunity_id": opportunity_id, "user_id": user_id, "fresh": fresh}
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]
