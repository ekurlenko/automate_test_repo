from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, ClassVar

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from adspower.errors import ApiDown, ApiRejected, NoDebugPort
from adspower.models import Endpoint
from adspower.settings import API_RETRIES, API_TIMEOUT, API_URL, RATE_LIMIT
from config import ADSPOWER_API_KEY

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

log = logging.getLogger(__name__)


class AdsPower:
    """Клиент Local API с троттлингом и ретраями транспорта."""

    START: ClassVar[str] = "/api/v1/browser/start"
    STOP: ClassVar[str] = "/api/v1/browser/stop"
    ACTIVE: ClassVar[str] = "/api/v1/browser/active"
    PROFILES: ClassVar[str] = "/api/v1/user/list"

    def __init__(  # noqa: PLR0913 — часы, сон и сессия подставляются в тестах
        self,
        api_url: str = API_URL,
        key: str | None = ADSPOWER_API_KEY,
        *,
        timeout: float = API_TIMEOUT,
        rate_limit: float = RATE_LIMIT,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_url = api_url.rstrip("/")
        self.key = key
        self.timeout = timeout
        self.rate_limit = rate_limit
        self._http = session or requests.Session()
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._last_call = float("-inf")  # не 0: первый запрос не должен ждать

    def _wait_turn(self) -> None:
        """Выдержать паузу: Local API принимает не чаще одного запроса в секунду."""
        with self._lock:
            idle = self._clock() - self._last_call
            if idle < self.rate_limit:
                self._sleep(self.rate_limit - idle)
            self._last_call = self._clock()

    @retry(  # только транспорт: логический отказ повторять незачем
        retry=retry_if_exception_type(ApiDown),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(API_RETRIES),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._wait_turn()
        # С версии 8.x ключ принимается только заголовком.
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        try:
            response = self._http.get(f"{self.api_url}{path}", params=params or {},
                                      headers=headers, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ApiDown(
                f"Local API недоступен ({self.api_url}): {exc}. "
                "Проверьте, что приложение AdsPower запущено и Local API включён."
            ) from exc

        # Отказы Local API приезжают с HTTP 200.
        if payload.get("code") != 0:
            raise ApiRejected(payload.get("code", -1), payload.get("msg", "неизвестная ошибка"))
        return payload.get("data") or {}

    def start(self, user_id: str, *, headless: bool = False) -> Endpoint:
        """Открыть профиль по ID и получить вебдрайвер с портом отладки."""
        data = self._get(self.START,
                         {"user_id": user_id, "headless": int(headless),
                          # стартовые вкладки сбивают выбор рабочей страницы
                          "open_tabs": 0, "ip_tab": 0})

        ws = data.get("ws") or {}
        if not ws.get("puppeteer"):
            raise NoDebugPort(
                f"профиль {user_id} запущен, но Local API не вернул точку отладки: {data}"
            )

        log.info("профиль %s запущен, debug_port=%s", user_id, data.get("debug_port"))
        return Endpoint(
            user_id=user_id,
            cdp_url=ws["puppeteer"],
            selenium=ws.get("selenium", ""),
            debug_port=str(data.get("debug_port", "")),
            webdriver=data.get("webdriver", ""),
        )

    def stop(self, user_id: str) -> None:
        """Закрыть профиль. Не бросает: вызывается из finally."""
        try:
            self._get(self.STOP, {"user_id": user_id})
            log.info("профиль %s остановлен", user_id)
        except Exception as exc:
            log.warning("профиль %s не остановился штатно: %s", user_id, exc)

    def running(self, user_id: str) -> bool:
        """Запущен ли профиль по данным Local API."""
        return self._get(self.ACTIVE, {"user_id": user_id}).get("status") == "Active"

    def profiles(self, page: int = 1, size: int = 100) -> list[dict[str, Any]]:
        """Список профилей: отсюда берётся user_id."""
        return self._get(self.PROFILES,
                         {"page": page, "page_size": min(size, 100)}).get("list", [])


@contextmanager
def launched(ads: AdsPower, user_id: str, **kwargs: Any) -> Iterator[Endpoint]:
    """Открыть профиль и закрыть на выходе: брошенные копятся процессами Chromium."""
    endpoint = ads.start(user_id, **kwargs)
    try:
        yield endpoint
    finally:
        ads.stop(user_id)
