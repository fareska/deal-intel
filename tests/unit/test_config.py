import pytest
from pydantic import ValidationError

from deal_intel.config import DEFAULT_MODEL_EXTRACTION, DEFAULT_MODEL_STRATEGY, Settings

DATABASE_URL = "postgresql+psycopg://user:secret@localhost:5432/example"


def test_settings_fail_fast_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_settings_read_database_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)

    settings = Settings(_env_file=None)

    assert str(settings.database_url) == DATABASE_URL


def test_default_models_are_the_confirmed_ids_and_are_priced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    for name in ("MODEL_STRATEGY", "MODEL_EXTRACTION", "MODEL_PRICES_USD_PER_MTOK"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.model_strategy == DEFAULT_MODEL_STRATEGY == "claude-opus-5-5"
    assert settings.model_extraction == DEFAULT_MODEL_EXTRACTION == "claude-haiku-4-5-20251001"
    assert settings.eval_tolerance == 0.02
    assert {DEFAULT_MODEL_STRATEGY, DEFAULT_MODEL_EXTRACTION} <= set(
        settings.model_prices_usd_per_mtok
    )


def test_routed_model_without_a_price_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("MODEL_STRATEGY", "claude-unpriced-model")

    with pytest.raises(ValidationError, match="claude-unpriced-model"):
        Settings(_env_file=None)
