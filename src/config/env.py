from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"

load_dotenv(ROOT / ".env", override=False)  # переменные окружения важнее файла


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


ADSPOWER_API_KEY = env("ADSPOWER_API_KEY") or None
ADSPOWER_PROFILE_ID = env("ADSPOWER_PROFILE_ID") or None
MOBILE_PROXY_ROTATE_URL = env("MOBILE_PROXY_ROTATE_URL") or None

GOOGLE_SERVICE_ACCOUNT_FILE = env("GOOGLE_SERVICE_ACCOUNT_FILE") or None
SPREADSHEET_ID = env("SPREADSHEET_ID") or None
