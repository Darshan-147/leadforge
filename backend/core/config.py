import logging
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_publishable_key: str = ""
    frontend_origin: str = "http://localhost:3000"
    max_pages_per_scan: int = 5
    request_timeout_seconds: float = 8.0
    hunter_api_key: str = ""
    hunter_api_base_url: str = "https://api.hunter.io/v2"
    hunter_verify_limit_per_scan: int = 5
    hunter_max_emails_per_scan: int | None = None
    hunter_cache_ttl_hours: int = 24
    hunter_timeout_seconds: float = 6.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    aws_s3_bucket: str = ""
    aws_region: str = "ap-south-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()
logger = logging.getLogger("leadforge")
