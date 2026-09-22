"""Точка входа блока 1: запустить профиль AdsPower и собрать товары кабинета.

    python scripts/run_ozon.py --profile jx1a2b3 --url https://seller.ozon.ru/app/products

Здесь собрана вся стратегия отказов целиком: профиль поднимается, IP
проверяется, сценарий выполняется, на капче — отступ со сменой IP, профиль
гасится в любом случае.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from ozon_test.adspower.client import DEFAULT_BASE_URL, AdsPowerClient
from ozon_test.adspower.errors import AdsPowerError, CaptchaDetected, PageStalled
from ozon_test.adspower.scenario import RetryPolicy, run_scenario
from ozon_test.adspower.session import browser_page, profile_ip, rotate_mobile_ip

log = logging.getLogger("run_ozon")

# Пауза после капчи. Возвращаться через 10 секунд бессмысленно: сигнал
# антифрода живёт дольше, чем наш ретрай.
COOLDOWN_AFTER_CAPTCHA = 180.0


def collect_once(client: AdsPowerClient, user_id: str, url: str, *, headless: bool) -> list:
    with browser_page(client, user_id, headless=headless) as (page, _context):
        ip = profile_ip(page)
        log.info("профиль %s работает с IP %s", user_id, ip)
        if not ip:
            raise AdsPowerError(
                f"профиль {user_id}: не удалось определить внешний IP — "
                "вероятно, прокси в профиле не поднялся. Работать нельзя."
            )
        return run_scenario(page, url, policy=RetryPolicy(attempts=3, base_delay=3.0))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.getenv("ADSPOWER_PROFILE_ID"), required=False)
    parser.add_argument("--url", default="https://seller.ozon.ru/app/products")
    parser.add_argument("--api-url", default=os.getenv("ADSPOWER_API_URL", DEFAULT_BASE_URL))
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--attempts", type=int, default=2, help="попыток после смены IP")
    args = parser.parse_args()

    if not args.profile:
        parser.error("нужен --profile или переменная ADSPOWER_PROFILE_ID")

    client = AdsPowerClient(args.api_url, api_key=os.getenv("ADSPOWER_API_KEY"))
    rotate_url = os.getenv("MOBILE_PROXY_ROTATE_URL")

    for attempt in range(1, args.attempts + 1):
        try:
            products = collect_once(client, args.profile, args.url, headless=args.headless)
        except CaptchaDetected as exc:
            # Единственная осмысленная реакция: отпустить профиль, сменить IP,
            # подождать. Решать капчу мы не пытаемся.
            log.warning("капча на %s (скриншот: %s)", exc.url, exc.screenshot)
            if attempt == args.attempts:
                log.error("капча не ушла после %s попыток — останавливаемся", attempt)
                return 2
            if rotate_url and rotate_mobile_ip(rotate_url):
                log.info("IP сменён, ждём %s с перед повтором", COOLDOWN_AFTER_CAPTCHA)
            else:
                log.warning("ротация IP недоступна, просто ждём")
            time.sleep(COOLDOWN_AFTER_CAPTCHA)
            continue
        except PageStalled as exc:
            log.error("страница так и не прогрузилась: %s", exc)
            return 3
        except AdsPowerError as exc:
            log.error("ошибка AdsPower: %s", exc)
            return 4

        for product in products:
            print(f"{product.article}\t{product.name}\t{product.price}\t{product.stock}")
        log.info("готово, собрано позиций: %s", len(products))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
