from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8004
    idle_interval_min: int = 20
    usermanager_url: str = "http://localhost:8005"
    database_url: str = "sqlite+aiosqlite:///./data/epaper.db"

    class Config:
        env_prefix = "EPAPER_"


settings = Settings()
