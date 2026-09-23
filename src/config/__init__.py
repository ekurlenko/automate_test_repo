"""Общая конфигурация: окружение и логирование."""

from config.env import (
    ADSPOWER_API_KEY,
    ADSPOWER_PROFILE_ID,
    DATA_DIR,
    GOOGLE_SERVICE_ACCOUNT_FILE,
    MOBILE_PROXY_ROTATE_URL,
    ROOT,
    SPREADSHEET_ID,
    env,
)
from config.logs import setup as setup_logging

__all__ = [
    "ADSPOWER_API_KEY",
    "ADSPOWER_PROFILE_ID",
    "DATA_DIR",
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "MOBILE_PROXY_ROTATE_URL",
    "ROOT",
    "SPREADSHEET_ID",
    "env",
    "setup_logging",
]
