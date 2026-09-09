from pathlib import Path
from typing import List, Union
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Root directory of the repository
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent


class Settings(BaseSettings):
    PROJECT_NAME: str = "Splitwise Buddy API"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # Database (reads DATABASE_URL)
    DATABASE_URL: str = Field(validation_alias=AliasChoices("DATABASE_URL", "POSTGRES_URL"))

    # Supabase Auth (reads SUPABASE_URL or VITE_SUPABASE_URL)
    SUPABASE_URL: str = Field(validation_alias=AliasChoices("SUPABASE_URL", "VITE_SUPABASE_URL"))
    SUPABASE_JWT_SECRET: str | None = Field(default=None, validation_alias=AliasChoices("SUPABASE_JWT_SECRET", "JWT_SECRET"))

    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = Field(default=120, validation_alias=AliasChoices("RATE_LIMIT_PER_MINUTE", "RATE_LIMIT"))
    RATE_LIMIT_ENABLED: bool = Field(default=True, validation_alias=AliasChoices("RATE_LIMIT_ENABLED"))

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)

    @property
    def async_database_url(self) -> str:
        """
        Ensures the connection string uses the asyncpg driver.
        Handles postgresql:// and postgres:// prefixes.
        """
        url = self.DATABASE_URL
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    @property
    def jwks_url(self) -> str:
        """Returns the Supabase Auth JWKS endpoint URL."""
        base = self.SUPABASE_URL.rstrip("/")
        return f"{base}/auth/v1/.well-known/jwks.json"

    model_config = SettingsConfigDict(
        env_file=(str(ROOT_DIR / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
