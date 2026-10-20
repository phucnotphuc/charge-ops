from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    mongodb_uri: str = "mongodb://mongo:27017"
    mongodb_database: str = "chargeops"
    simulator_secret: SecretStr = Field(min_length=24)
    webhook_secret: SecretStr = Field(min_length=24)
    api_url: str = "http://api:8000"
    dd_agent_host: str = "datadog"
    dd_monitor_id: str = ""
    dd_trial_id: str = "hackathon-2026"
    log_dir: str = "logs"


@lru_cache
def settings():
    return Settings()
