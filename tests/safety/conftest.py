from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tests.unit.test_agents import AgentBench


@pytest.fixture
def bench(
    ingested_session: Session, session_factory: sessionmaker[Session], tmp_path: Path
) -> AgentBench:
    return AgentBench(ingested_session, session_factory, tmp_path)
