"""
Central configuration, loaded from environment variables (.env).
Keeping every tunable in one place — model tiers, retry counts, the
dry-run switch — makes it easy to reason about cost and behavior without
hunting through the codebase.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", protected_namespaces=("settings_",))

    # OrcaRouter — https://api.orcarouter.ai/v1, OpenAI-compatible.
    orcarouter_api_key: str = "unset"
    orcarouter_base_url: str = "https://api.orcarouter.ai/v1"

    # Model tiers. The cheap model handles the common case; the strong
    # model is only paid for when the cheap model is unsure or fails
    # validation.
    model_cheap: str = "openai/gpt-4o-mini"
    model_strong: str = "anthropic/claude-opus-4.7"
    model_vision: str = "openai/gpt-4o-mini"

    api_key: str = "demo-local-api-key"
    database_url: str = "sqlite:///./kanjoai.db"

    # When true, no network call to OrcaRouter is made — a deterministic
    # stub response is returned instead. Lets the whole pipeline (and the
    # demo) run before a real OrcaRouter key is wired up.
    dry_run: bool = True

    # Business rule: invoices within this % of their PO amount auto-approve.
    auto_approve_tolerance_pct: float = 2.0
    # Confidence below this triggers escalation to the strong model.
    confidence_escalation_threshold: float = 0.75


settings = Settings()
