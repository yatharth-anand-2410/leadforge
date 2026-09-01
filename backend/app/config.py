"""Application settings loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    # Which chat model backend the discovery agent uses. One of:
    #   groq | gemini | opencode-openai | opencode-anthropic
    llm_provider: str = "groq"
    llm_model: str = "llama-3.1-8b-instant"
    groq_api_key: str = ""
    gemini_api_key: str = ""  # native Gemini API (Google AI Studio)
    opencode_api_key: str = ""  # OpenCode Go; shared by both opencode styles
    opencode_base_url: str = "https://opencode.ai/zen/go/v1"

    # Database
    database_url: str = "sqlite:///./leadforge.db"

    # Auth
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    # Data sources
    overpass_endpoint: str = "https://overpass-api.de/api/interpreter"
    nominatim_endpoint: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "leadforge"
    # False disables the OSM/Nominatim (Overpass) sources entirely, making every
    # `search_businesses` call web-only (SearXNG). Requires SEARXNG_ENDPOINT.
    enable_osm: bool = True

    # Free web search via SearXNG metasearch. Empty string disables web search
    # (Overpass/Nominatim still run). Public instances are rate-limited; run
    # your own for stable, unfiltered JSON API access.
    searxng_endpoint: str = ""
    searxng_timeout: int = 15

    # Web
    cors_origins: str = "http://localhost:3000"

    # Observability (LangSmith)
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "leadforge"
    langsmith_endpoint: str = ""  # optional; empty = LangSmith SaaS (US)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
