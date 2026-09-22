"""Жизненный цикл профиля и подключение Playwright к запущенному браузеру.

Ключевая деталь всей задачи: браузер запускает AdsPower, а не Playwright.
`playwright.chromium.launch()` поднял бы чистый Chromium — без отпечатка, без
прокси, без кук кабинета, то есть ровно то, ради чего антидетект и нужен.
Поэтому используется `connect_over_cdp()` — подключение к чужому процессу.

Вторая деталь: после подключения берём СУЩЕСТВУЮЩИЙ контекст
(`browser.contexts[0]`). Новый контекст завёл бы чистый профиль внутри
запущенного браузера и обнулил бы авторизацию в кабинете.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import requests

from ozon_test.adspower.client import AdsPowerClient, BrowserEndpoint

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

log = logging.getLogger(__name__)


@contextmanager
def profile(client: AdsPowerClient, user_id: str, **start_kwargs: Any) -> Iterator[BrowserEndpoint]:
    """Запустить профиль и гарантированно остановить его на выходе.

    Без `finally` брошенные профили копятся: AdsPower держит каждый как живой
    процесс Chromium, и через десяток прогонов машина встаёт.
    """
    endpoint = client.start(user_id, **start_kwargs)
    try:
        yield endpoint
    finally:
        client.stop(user_id)


@contextmanager
def browser_page(
    client: AdsPowerClient,
    user_id: str,
    *,
    default_timeout_ms: int = 30_000,
    **start_kwargs: Any,
) -> Iterator[tuple["Page", "BrowserContext"]]:
    """Полный цикл: профиль -> CDP -> готовая вкладка -> остановка профиля."""
    from playwright.sync_api import sync_playwright

    with profile(client, user_id, **start_kwargs) as endpoint:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint.cdp_url)
            # Контекст профиля уже существует — забираем его, а не создаём новый.
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            context.set_default_timeout(default_timeout_ms)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                yield page, context
            finally:
                # Закрываем только соединение. Сам браузер гасит AdsPower через
                # /browser/stop — иначе профиль не успеет сохранить куки.
                browser.close()


def current_ip(proxy_check_url: str = "https://api.ipify.org?format=json", timeout: float = 10.0) -> str:
    """Внешний IP текущего процесса — для сверки с IP профиля."""
    response = requests.get(proxy_check_url, timeout=timeout)
    response.raise_for_status()
    return response.json().get("ip", "")


def profile_ip(page: "Page", proxy_check_url: str = "https://api.ipify.org?format=json") -> str:
    """Внешний IP, с которого ходит профиль.

    Проверка нужна до начала работы: если прокси в профиле отвалился, AdsPower
    молча выпустит трафик с домашнего IP — и кабинет улетит в бан.
    """
    response = page.request.get(proxy_check_url)
    return response.json().get("ip", "")


def rotate_mobile_ip(rotate_url: str, timeout: float = 30.0) -> bool:
    """Сменить IP мобильного прокси через его rotate-ссылку.

    У мобильных прокси смена IP — это HTTP-запрос к ссылке ротации из ЛК
    провайдера. Вызывается только при реальном подозрении на блок: у операторов
    ограничение на частоту ротации (обычно не чаще раза в 2 минуты).
    """
    try:
        response = requests.get(rotate_url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        log.warning("не удалось сменить IP мобильного прокси: %s", exc)
        return False
    log.info("IP мобильного прокси сменён")
    return True
