from __future__ import annotations

from config import env

API_URL = env("ADSPOWER_API_URL", "http://local.adspower.net:50325")
# Local API отвечает `Too many request` чаще раза в секунду.
RATE_LIMIT = 1.0
API_TIMEOUT = 30.0
API_RETRIES = 3
