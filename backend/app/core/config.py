from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI 截流雷达"
    database_url: str = "sqlite:///./data/lead_radar.db"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.2
    llm_timeout: float = 45.0
    douyin_comments_crawler_url: str = "http://127.0.0.1:8000"
    mediacrawler_path: str = ""
    social_harvest_path: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
