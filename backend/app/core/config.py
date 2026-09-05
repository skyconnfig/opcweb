from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "AI 截流雷达"
    database_url: str = "sqlite:///./data/lead_radar.db"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.2
    llm_timeout: float = 45.0
    content_provider: str = "douyin-playwright"
    api_auth_token: str = ""
    settings_encryption_key: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    douyin_comments_crawler_url: str = "http://127.0.0.1:8000"
    mediacrawler_path: str = ""
    social_harvest_path: str = ""
    douyin_profile_dir: str = str(PROJECT_ROOT / "data" / "browser" / "douyin")
    douyin_headless: bool = False
    douyin_browser_channel: str = "chromium"
    douyin_proxy_server: str = ""
    douyin_default_comment_limit: int = 100
    auto_reply_enabled: bool = False
    auto_reply_max_per_hour: int = 10
    auto_reply_max_per_day: int = 50
    auto_reply_min_interval_seconds: int = 30
    # Resolve the repository .env by module location, not process cwd. This
    # keeps the same encrypted settings and browser profile when uvicorn is
    # launched from the repository root, backend/, or a shortcut.
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
