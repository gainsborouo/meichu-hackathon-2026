from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, overridable via environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    project_name: str = "meichu-hackathon-2026 API"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]

    # Statement uploads are held in memory and a temp file only for the length
    # of one request, so the ceiling exists to bound that, not storage.
    max_upload_bytes: int = 10 * 1024 * 1024
    max_statement_files: int = 12
    statement_timeout_seconds: float = 300.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
