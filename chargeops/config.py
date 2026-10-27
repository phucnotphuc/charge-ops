from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    mongodb_uri: str = "mongodb://mongo:27017"
    mongodb_database: str = "chargeops"
    simulator_secret: SecretStr = Field(min_length=24)
    webhook_secret: SecretStr = Field(min_length=24)
    operator_secret: SecretStr = Field(min_length=24)
    operator_username: str = "operator"
    operator_password_hash: SecretStr = SecretStr("")
    allowed_origins: str = "http://localhost:8080,http://localhost:5173"
    api_url: str = "http://api:8000"
    dd_agent_host: str = "datadog"
    dd_monitor_id: str = ""
    dd_trial_id: str = "hackathon-2026"
    dd_metrics_enabled: bool = False
    log_dir: str = "logs"

    @field_validator("operator_password_hash", mode="before")
    @classmethod
    def unescape_compose(cls, value):
        # init_env.py writes '$$' for Compose; read without Compose (uvicorn + Vite) the '$$' stays literal.
        # A scrypt hash never contains '$$' (every field is non-empty), so this is a no-op inside Compose.
        return value.replace("$$", "$") if isinstance(value, str) else value


@lru_cache
def settings():
    return Settings()
