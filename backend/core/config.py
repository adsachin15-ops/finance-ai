"""
backend/core/config.py
─────────────────────────────────────────────────────────────
Central configuration management using Pydantic Settings.
Reads from .env file. Validated at startup.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        protected_namespaces=(),
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "Finance-AI"
    app_version: str = "1.0.0"
    app_env: Literal["development", "production", "testing"] = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Database ---
    db_encryption_key: str = Field(..., min_length=32)
    db_path: Path = Path("database/finance.db")
    guest_db_path: str = ""

    # --- Security ---
    secret_key: str = Field(..., min_length=32)
    session_expire_hours: int = 24
    pin_min_length: int = 4
    pin_max_length: int = 8

    # --- File Upload ---
    max_file_size_mb: int = 10
    allowed_extensions: str = "csv,xlsx,pdf"
    upload_temp_dir: Path = Path("uploads/temp")

    # --- AI ---
    model_path: Optional[str] = None

    # --- Reminders ---
    reminder_check_interval_minutes: int = 60

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: Optional[Path] = Path("logs/finance-ai.log")
    log_format: Literal["json", "console"] = "console"

    # --- Phase 2 Cloud ---
    cloud_api_url: Optional[str] = None
    cloud_api_key: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None

    # --- Validators ---

    @field_validator("allowed_extensions", mode="before")
    @classmethod
    def parse_extensions(cls, v: str) -> str:
        return ",".join(e.strip().lower() for e in v.split(","))

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.app_env == "production":
            if self.debug:
                raise ValueError("DEBUG must be False in production.")
        return self

    # --- Properties ---

    @property
    def allowed_ext_set(self) -> set[str]:
        return set(self.allowed_extensions.split(","))

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cloud_enabled(self) -> bool:
        return bool(self.cloud_api_url and self.cloud_api_key)

    def ensure_dirs(self) -> None:
        dirs = [self.db_path.parent, self.upload_temp_dir]
        if self.log_file:
            dirs.append(self.log_file.parent)
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
