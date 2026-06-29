from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "OpenSecDash"

    database_url: str = "sqlite:///./opensecdash.db"
    auto_migrate: bool = True

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
