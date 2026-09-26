from sqlalchemy.orm import Session, sessionmaker

from deal_intel.config import LlmClientKind, Settings
from deal_intel.llm.client import LlmClient
from deal_intel.llm.fake_client import FakeLlmProvider
from deal_intel.observability.tracing import Tracer


def build_llm_client(
    settings: Settings, session_factory: sessionmaker[Session], tracer: Tracer
) -> LlmClient:
    if settings.llm_client is LlmClientKind.FAKE:
        provider = FakeLlmProvider(settings.llm_fixtures_root)
        return LlmClient(
            provider, settings=settings, session_factory=session_factory, tracer=tracer
        )
    # Imported here so fake-client processes never load the SDK.
    from deal_intel.llm.anthropic_client import AnthropicProvider

    return LlmClient(
        AnthropicProvider(settings),
        settings=settings,
        session_factory=session_factory,
        tracer=tracer,
        record_fixtures_to=settings.llm_fixtures_root if settings.record_fixtures else None,
    )
