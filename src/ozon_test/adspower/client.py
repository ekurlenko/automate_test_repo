"""Клиент AdsPower Local API.

AdsPower поднимает HTTP-сервер на localhost (по умолчанию порт 50325). Запуск
профиля через него — единственный способ получить браузер с нужным отпечатком
и уже подключённым прокси: эти настройки живут в профиле, а не в коде.

Два ограничения API, которые определяют устройство клиента:

* не чаще одного запроса в секунду — иначе `code=-1, Too many request`;
* HTTP 200 ничего не значит, смотреть надо на поле `code` в теле ответа.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ozon_test.adspower.errors import ApiRejected, LocalApiUnavailable, ProfileStartError

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://local.adspower.net:50325"

# Официальное ограничение Local API: не чаще 1 запроса в секунду.
MIN_REQUEST_INTERVAL = 1.0


@dataclass(frozen=True)
class BrowserEndpoint:
    """Точки подключения к уже запущенному браузеру профиля."""

    user_id: str
    ws_puppeteer: str
    selenium: str
    webdriver: str
    debug_port: str

    @property
    def cdp_url(self) -> str:
        """Адрес для `playwright.chromium.connect_over_cdp()`."""
        return self.ws_puppeteer


class AdsPowerClient:
    """Обёртка над Local API.

    Троттлинг встроен в клиент, а не в вызывающий код: про лимит легко забыть,
    а ловится он неинформативным `code=-1` на случайном запросе.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        *,
        timeout: float = 30.0,
        min_interval: float = MIN_REQUEST_INTERVAL,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.min_interval = min_interval
        self._session = session or requests.Session()
        self._clock = clock
        self._sleep = sleeper
        self._lock = threading.Lock()
        # -inf, а не 0: иначе самый первый запрос честно отстоит полную паузу,
        # хотя перед ним ничего не было.
        self._last_request_at = float("-inf")

    def _throttle(self) -> None:
        with self._lock:
            elapsed = self._clock() - self._last_request_at
            if elapsed < self.min_interval:
                self._sleep(self.min_interval - elapsed)
            self._last_request_at = self._clock()

    @retry(
        # Ретраим только транспорт: таймаут, обрыв соединения. Логические отказы
        # API (ApiRejected) повторять бессмысленно — ответ не изменится.
        retry=retry_if_exception_type(LocalApiUnavailable),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._throttle()
        query = dict(params or {})
        if self.api_key:
            query["api_key"] = self.api_key

        try:
            response = self._session.get(
                f"{self.base_url}{path}", params=query, timeout=self.timeout
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise LocalApiUnavailable(
                f"Local API недоступен ({self.base_url}): {exc}. "
                "Проверьте, что приложение AdsPower запущено и Local API включён."
            ) from exc

        # Главная ловушка Local API: ошибки приезжают с HTTP 200.
        if payload.get("code") != 0:
            raise ApiRejected(payload.get("code", -1), payload.get("msg", "неизвестная ошибка"))
        return payload.get("data") or {}

    def start(
        self,
        user_id: str,
        *,
        headless: bool = False,
        open_tabs: bool = False,
        ip_tab: bool = False,
    ) -> BrowserEndpoint:
        """Запустить профиль и получить адрес отладочного порта.

        open_tabs=False и ip_tab=False — чтобы не открывать стартовые вкладки и
        вкладку проверки IP: они сбивают выбор страницы при подключении.
        """
        data = self._request(
            "/api/v1/browser/start",
            {
                "user_id": user_id,
                "headless": int(headless),
                "open_tabs": int(open_tabs),
                "ip_tab": int(ip_tab),
            },
        )

        ws = (data.get("ws") or {}).get("puppeteer", "")
        if not ws:
            raise ProfileStartError(
                f"профиль {user_id} запущен, но Local API не вернул точку отладки: {data}"
            )

        log.info("профиль %s запущен, debug_port=%s", user_id, data.get("debug_port"))
        return BrowserEndpoint(
            user_id=user_id,
            ws_puppeteer=ws,
            selenium=(data.get("ws") or {}).get("selenium", ""),
            webdriver=data.get("webdriver", ""),
            debug_port=str(data.get("debug_port", "")),
        )

    def stop(self, user_id: str) -> None:
        """Остановить профиль. Не бросает: вызывается из finally."""
        try:
            self._request("/api/v1/browser/stop", {"user_id": user_id})
            log.info("профиль %s остановлен", user_id)
        except Exception as exc:  # noqa: BLE001 — падение остановки не должно ломать сценарий
            log.warning("не удалось корректно остановить профиль %s: %s", user_id, exc)

    def is_active(self, user_id: str) -> bool:
        """Проверить, запущен ли профиль (`status == "Active"`)."""
        data = self._request("/api/v1/browser/active", {"user_id": user_id})
        return data.get("status") == "Active"

    def list_profiles(self, page: int = 1, page_size: int = 100) -> list[dict[str, Any]]:
        """Список профилей группы — нужен, чтобы гонять сценарий по всем кабинетам."""
        data = self._request(
            "/api/v1/user/list", {"page": page, "page_size": min(page_size, 100)}
        )
        return data.get("list", [])
