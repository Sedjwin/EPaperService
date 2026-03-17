from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8004
    # Idle refresh interval in minutes (refresh happens at multiples of this on the hour)
    idle_interval_min: int = 20

    class Config:
        env_prefix = "EPAPER_"


settings = Settings()
