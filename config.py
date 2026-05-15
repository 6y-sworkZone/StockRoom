from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    SECRET_KEY: str = "your-secret-key-here-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    DATABASE_URL: str = "sqlite+aiosqlite:///./stockroom.db"

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings():
    return Settings()
