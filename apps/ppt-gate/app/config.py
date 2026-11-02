from __future__ import annotations

import os
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEVELOPMENT_ENVS = {"dev", "development", "test", "testing", "local"}


def is_production_like(environment: str | None) -> bool:
    return str(environment or "dev").strip().lower() not in _DEVELOPMENT_ENVS


class Settings(BaseSettings):
    """Runtime configuration for the PPT gate.

    Values are read from environment variables or a local ``.env`` file.
    Secrets are never written to logs.
    """

    app_env: str = "dev"
    database_path: str = "data/ppt_gate.db"
    public_base_url: str = "http://127.0.0.1:8009"

    presenton_base_url: str = "http://127.0.0.1:5001"
    presenton_admin_username: str = ""
    presenton_admin_secret: str = ""
    presenton_timeout_seconds: float = 60.0

    gate_secret: str = ""
    card_pepper: str = ""
    gate_admin_token: str = ""

    session_ttl_seconds: int = 30 * 24 * 60 * 60
    card_default_valid_days: int = 7
    cookie_secure: bool = False
    cookie_domain: str | None = None
    redeem_landing_enabled: bool = True
    proxy_read_timeout_seconds: float = 600.0
    max_batch_size: int = 1000

    # Metering and budget. A daily budget of -1 means unlimited.
    metering_enabled: bool = True
    deny_unknown_writes: bool = False
    default_daily_budget_cents: int = 10000
    estimated_generation_cents: int = 20
    estimated_ai_edit_cents: int = 5
    estimated_image_cents: int = 10
    max_concurrent_generations: int = 2
    max_concurrent_generations_per_card: int = 1
    reservation_ttl_seconds: int = 1800
    settlement_interval_seconds: int = 10
    max_metered_body_bytes: int = 2 * 1024 * 1024

    # Xianyu card-group import (front-end user API, not the admin API).
    xianyu_import_enabled: bool = False
    xianyu_api_base_url: str = ""
    xianyu_username: str = ""
    xianyu_secret: str = ""
    xianyu_card_group_id: int | None = None
    xianyu_card_group_name: str = "AI PPT 兑换码"
    xianyu_timeout_seconds: float = 30.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def effective_card_pepper(self) -> str:
        return self.card_pepper or self.gate_secret

    @property
    def production_like(self) -> bool:
        return is_production_like(self.app_env)

    @model_validator(mode="after")
    def validate_security_defaults(self) -> "Settings":
        if self.production_like:
            missing = [
                name
                for name, value in (
                    ("GATE_SECRET", self.gate_secret),
                    ("CARD_PEPPER", self.effective_card_pepper),
                    ("GATE_ADMIN_TOKEN", self.gate_admin_token),
                    ("PRESENTON_ADMIN_USERNAME", self.presenton_admin_username),
                    ("PRESENTON_ADMIN_SECRET", self.presenton_admin_secret),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "missing required production settings: " + ", ".join(missing)
                )
            for name, value in (
                ("GATE_SECRET", self.gate_secret),
                ("CARD_PEPPER", self.effective_card_pepper),
                ("GATE_ADMIN_TOKEN", self.gate_admin_token),
            ):
                if len(value) < 32:
                    raise ValueError(f"{name} must be at least 32 characters")
            if self.metering_enabled and self.default_daily_budget_cents < -1:
                raise ValueError("DEFAULT_DAILY_BUDGET_CENTS must be -1 or greater")
            if self.xianyu_import_enabled:
                xianyu_missing = [
                    name
                    for name, value in (
                        ("XIANYU_API_BASE_URL", self.xianyu_api_base_url),
                        ("XIANYU_USERNAME", self.xianyu_username),
                        ("XIANYU_SECRET", self.xianyu_secret),
                    )
                    if not value
                ]
                if xianyu_missing:
                    raise ValueError(
                        "missing Xianyu import settings: " + ", ".join(xianyu_missing)
                    )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
    os.environ.pop("__PPT_GATE_SETTINGS_TEST__", None)
