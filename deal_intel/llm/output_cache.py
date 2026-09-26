"""Cross-run output cache. Each lookup and store uses its own short session, so a cached output
survives even if the caller's transaction later rolls back."""

from sqlalchemy.orm import Session, sessionmaker

from deal_intel.contracts.llm import CachedOutput
from deal_intel.db.models import AgentOutputCacheRow
from deal_intel.db.writes import column_values, upsert_rows


def lookup(session_factory: sessionmaker[Session], key: str) -> CachedOutput | None:
    with session_factory() as session:
        row = session.get(AgentOutputCacheRow, key)
        if row is None:
            return None
        return CachedOutput(
            cache_key=row.cache_key,
            agent_name=row.agent_name,
            prompt_version=row.prompt_version,
            prompt_hash=row.prompt_hash,
            model=row.model,
            input_hash=row.input_hash,
            output_json=row.output_json,
            raw_text=row.raw_text,
            guardrail_results=row.guardrail_results,
            tool_evidence_ids=row.tool_evidence_ids,
        )


def store(session_factory: sessionmaker[Session], entry: CachedOutput) -> None:
    with session_factory.begin() as session:
        upsert_rows(session, AgentOutputCacheRow, [column_values(entry)])
