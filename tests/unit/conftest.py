import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from deal_intel.contracts.evidence import EvidenceChunk
from deal_intel.contracts.reference import LowMediumHigh
from deal_intel.retrieval.ingest import build_chunks, build_context, load_evidence
from deal_intel.retrieval.reference import ReferenceData, load_reference_data, read_reference_data
from deal_intel.retrieval.sensitivity import SensitivityRule
from deal_intel.retrieval.slack_dataset import write_slack_dataset

REPO_ROOT = Path(__file__).resolve().parents[2]
CHUNK_TEST_SNAPSHOT_ID = "chunk-test"


@pytest.fixture(scope="session")
def synthetic_data() -> Path:
    return REPO_ROOT / "synthetic_data"


@pytest.fixture(scope="session")
def dataset_root(synthetic_data: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the dataset with the Slack file generated, so tests never depend on the
    committed file and never write into the real dataset."""
    root = tmp_path_factory.mktemp("dataset") / "synthetic_data"
    shutil.copytree(synthetic_data, root)
    write_slack_dataset(root)
    return root


@pytest.fixture(scope="session")
def reference(synthetic_data: Path) -> ReferenceData:
    return read_reference_data(synthetic_data)


@pytest.fixture(scope="session")
def sensitivity() -> SensitivityRule:
    return SensitivityRule(not_required_status="not_required", high_risk_level=LowMediumHigh.HIGH)


@pytest.fixture(scope="session")
def evidence_chunks(dataset_root: Path, sensitivity: SensitivityRule) -> list[EvidenceChunk]:
    return build_chunks(build_context(dataset_root, CHUNK_TEST_SNAPSHOT_ID, sensitivity))


@pytest.fixture
def ingested_session(
    db_session: Session, dataset_root: Path, sensitivity: SensitivityRule
) -> Session:
    load_reference_data(db_session, dataset_root)
    load_evidence(db_session, dataset_root, sensitivity)
    return db_session
