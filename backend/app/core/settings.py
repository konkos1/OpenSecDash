from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.input_limits import MAX_REQUEST_BODY_BYTES


class Settings(BaseSettings):
    app_name: str = "OpenSecDash"

    database_url: str = "sqlite:///./opensecdash.db"
    auto_migrate: bool = True
    max_request_body_bytes: int = MAX_REQUEST_BODY_BYTES

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
