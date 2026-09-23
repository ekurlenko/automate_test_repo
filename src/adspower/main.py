from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from adspower.client import AdsPower, launched
from adspower.errors import AdsPowerError
from adspower.settings import API_URL
from config import ADSPOWER_PROFILE_ID, setup_logging

if TYPE_CHECKING:
    from adspower.models import Endpoint

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Открыть профиль AdsPower по ID и получить вебдрайвер и порт отладки"
    )
    parser.add_argument("--profile", default=ADSPOWER_PROFILE_ID)
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--keep-open", action="store_true",
                        help="не закрывать профиль после вывода")
    args = parser.parse_args(argv)
    if not args.profile:
        parser.error("нужен --profile или ADSPOWER_PROFILE_ID в .env")

    ads = AdsPower(args.api_url)
    try:
        if args.keep_open:
            _show(ads.start(args.profile, headless=args.headless))
        else:
            with launched(ads, args.profile, headless=args.headless) as endpoint:
                _show(endpoint)
    except AdsPowerError as exc:
        log.error("%s", exc)
        return 1
    return 0


def _show(endpoint: Endpoint) -> None:
    print(f"user_id:    {endpoint.user_id}")
    print(f"webdriver:  {endpoint.webdriver}")
    print(f"debug_port: {endpoint.debug_port}")
    print(f"cdp:        {endpoint.cdp_url}")
    print(f"selenium:   {endpoint.selenium}")
