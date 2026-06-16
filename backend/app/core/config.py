"""
app/core/config.py
------------------
Single place for all environment-variable settings.
Every other module imports `settings` from here — never reads os.environ directly.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── Azure Entra ID ────────────────────────────────────────────────────────
    AZURE_TENANT_ID: str
    AZURE_CLIENT_ID: str

    # ── App ───────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    APP_NAME: str = "HR Analytics API"
    API_VERSION: str = "v1"
    CORS_ORIGINS: str = "http://localhost:4200"

    # ── ML ───────────────────────────────────────────────────────────────────
    ML_AUC_GATE: float = 0.60
    ML_TRAIN_LOOKBACK_MONTHS: int = 36

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS_ORIGINS into a list."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.APP_ENV == "development"


@lru_cache()
def get_settings() -> Settings:
    """
    Cached settings instance.
    Use: from app.core.config import get_settings; settings = get_settings()
    """
    return Settings()