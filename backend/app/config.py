"""Application settings loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    groq_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"

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

    # Web
    cors_origins: str = "http://localhost:3000"

    # Agent limits
    max_agent_iterations: int = 60
    job_timeout_seconds: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
