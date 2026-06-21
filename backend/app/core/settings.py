from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "OpenSecDash"

    database_url: str = "sqlite:///./opensecdash.db"

    class Config:
        env_file = ".env"


settings = Settings()
