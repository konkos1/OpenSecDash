from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "OpenSecDash"

    database_url: str = "sqlite:///./opensecdash.db"
    auto_migrate: bool = True

    class Config:
        env_file = ".env"


settings = Settings()
