"""Settings loader. Pydantic-validated, env-driven, never logged."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = Field(default="dev")
    log_level: str = Field(default="INFO")

    mcp_base_url: str = Field(default="https://www.steppebusinessclub.com/api/mcp")
    mcp_team_token: SecretStr = Field(default=SecretStr("missing"))

    telegram_owner_bot_token: SecretStr = Field(default=SecretStr("missing"))
    telegram_owner_chat_id: str = Field(default="0")

    public_base_url: str = Field(default="http://localhost:8001")

    whatsapp_verify_token: SecretStr = Field(default=SecretStr("local-dev-verify-token"))
    whatsapp_app_secret: SecretStr = Field(default=SecretStr("local-dev-app-secret"))
    instagram_verify_token: SecretStr = Field(default=SecretStr("local-dev-verify-token"))
    instagram_app_secret: SecretStr = Field(default=SecretStr("local-dev-app-secret"))

    database_url: str = Field(default="sqlite:///./happycake.sqlite")
    web_port: int = Field(default=8000)
    gateway_port: int = Field(default=8001)

    claude_cli: str = Field(default="claude")
    claude_timeout_seconds: int = Field(default=45)

    reveal_token_salt: SecretStr = Field(default=SecretStr("happycake-reveal-default-salt"))

    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])

    def is_dev(self) -> bool:
        return self.env.lower() == "dev"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
