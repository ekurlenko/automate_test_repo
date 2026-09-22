"""Целевое действие в кабинете Ozon и защита от трёх типовых сбоев.

Уровни обработки ошибок (см. docs/ANSWER.md):

1. Транспорт  — берёт на себя AdsPowerClient: троттлинг и ретраи Local API.
2. Страница   — здесь: таймаут селектора, зависший SPA, повторная загрузка.
3. Антибот    — здесь: капча/блок. Не ретраим, а отступаем и меняем IP.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from ozon_test.adspower.errors import CaptchaDetected, PageStalled

if TYPE_CHECKING:
    from playwright.sync_api import Page

log = logging.getLogger(__name__)

T = TypeVar("T")

# Признаки капчи и страниц блокировки Ozon. Проверяем и разметку, и текст:
# антибот-страницы регулярно меняют вёрстку, но формулировки живут дольше.
CAPTCHA_SELECTORS: tuple[str, ...] = (
    "#challenge-running",
    "#reload-button",
    "[data-widget='captcha']",
    "form#challenge-form",
    "iframe[src*='captcha']",
)
CAPTCHA_TEXTS: tuple[str, ...] = (
    "Доступ ограничен",
    "подозрительная активность",
    "Вы не робот",
    "Проверка безопасности",
)
CAPTCHA_URL_MARKERS: tuple[str, ...] = ("/challenge", "captcha", "blocked")


class PageLike(Protocol):
    """Часть Playwright Page, которой пользуется сценарий.

    Протокол вместо конкретного типа — чтобы логика ретраев и детекта капчи
    тестировалась без браузера.
    """

    url: str

    def goto(self, url: str, **kwargs: Any) -> Any: ...
    def content(self) -> str: ...
    def query_selector(self, selector: str) -> Any: ...
    def query_selector_all(self, selector: str) -> list[Any]: ...
    def wait_for_selector(self, selector: str, **kwargs: Any) -> Any: ...
    def reload(self, **kwargs: Any) -> Any: ...
    def screenshot(self, **kwargs: Any) -> Any: ...


@dataclass
class RetryPolicy:
    """Сколько раз и с какими паузами пробовать снова."""

    attempts: int = 3
    base_delay: float = 2.0
    max_delay: float = 30.0
    # Джиттер обязателен: ровные паузы сами по себе выглядят как автоматизация.
    jitter: float = 0.5
    sleep: Callable[[float], None] = field(default=time.sleep)

    def delay_for(self, attempt: int) -> float:
        raw = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return raw * (1 + random.uniform(0, self.jitter))


def looks_like_captcha(page: PageLike) -> bool:
    """Определить, что вместо кабинета нам показали капчу или блок."""
    url = (page.url or "").lower()
    if any(marker in url for marker in CAPTCHA_URL_MARKERS):
        return True
    if any(page.query_selector(selector) for selector in CAPTCHA_SELECTORS):
        return True
    content = page.content() or ""
    return any(text.lower() in content.lower() for text in CAPTCHA_TEXTS)


def capture_artifact(page: PageLike, name: str, directory: str | Path = "artifacts") -> str | None:
    """Снять скриншот для разбора. Провал скриншота не должен ронять сценарий."""
    path = Path(directory) / f"{name}-{int(time.time())}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), full_page=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("не удалось снять скриншот %s: %s", path, exc)
        return None
    return str(path)


def guard_antibot(page: PageLike, *, artifacts_dir: str | Path = "artifacts") -> None:
    """Бросить CaptchaDetected, если на странице капча.

    Сценарий не пытается её решать. Практически это тупик, а с точки зрения
    правил площадки — прямое нарушение. Правильная реакция: зафиксировать,
    отпустить профиль, сменить IP и вернуться позже.
    """
    if looks_like_captcha(page):
        raise CaptchaDetected(page.url, capture_artifact(page, "captcha", artifacts_dir))


def open_page(
    page: PageLike,
    url: str,
    ready_selector: str,
    *,
    policy: RetryPolicy | None = None,
    timeout_ms: int = 30_000,
    artifacts_dir: str | Path = "artifacts",
) -> None:
    """Открыть страницу и дождаться готовности контента.

    `domcontentloaded` вместо `networkidle`: кабинет Ozon держит открытыми
    websocket'ы и поллинг, сеть у него не затихает никогда — networkidle почти
    гарантированно уйдёт в таймаут. Готовность определяем по ключевому селектору.
    """
    policy = policy or RetryPolicy()
    last_error: Exception | None = None

    for attempt in range(1, policy.attempts + 1):
        try:
            if attempt == 1:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            else:
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)

            # Проверка на антибот идёт ПЕРЕД ожиданием селектора: иначе мы просто
            # потратим полный таймаут, глядя на страницу капчи.
            guard_antibot(page, artifacts_dir=artifacts_dir)
            page.wait_for_selector(ready_selector, timeout=timeout_ms)
            return
        except CaptchaDetected:
            raise  # наверх без ретраев — этим занимается вызывающий код
        except Exception as exc:  # noqa: BLE001 — TimeoutError Playwright и родня
            last_error = exc
            log.warning("попытка %s/%s открыть %s не удалась: %s", attempt, policy.attempts, url, exc)
            if attempt < policy.attempts:
                policy.sleep(policy.delay_for(attempt))

    capture_artifact(page, "stalled", artifacts_dir)
    raise PageStalled(
        f"{url}: селектор {ready_selector!r} не появился за {policy.attempts} попыток "
        f"({last_error})"
    )


def human_pause(policy: RetryPolicy | None = None, low: float = 0.8, high: float = 2.4) -> None:
    """Случайная пауза между действиями.

    Не «обход защиты», а снижение нагрузки и имитация нормального темпа работы:
    серия кликов через 50 мс — самый дешёвый способ получить блок.
    """
    (policy.sleep if policy else time.sleep)(random.uniform(low, high))


@dataclass(frozen=True)
class ProductRow:
    """Строка, которую собираем со страницы товаров кабинета."""

    article: str
    name: str
    price: str
    stock: str


def collect_products(
    page: PageLike,
    *,
    row_selector: str = "[data-widget='productsList'] [data-testid='product-row']",
) -> list[ProductRow]:
    """Собрать таблицу товаров с текущей страницы.

    Данные читаем из DOM по атрибутам, а не по позиции колонок: вёрстка кабинета
    меняется часто, а data-атрибуты переживают редизайн.
    """
    rows: list[ProductRow] = []
    for element in page.query_selector_all(row_selector):
        rows.append(
            ProductRow(
                article=_text(element, "[data-testid='article']"),
                name=_text(element, "[data-testid='title']"),
                price=_text(element, "[data-testid='price']"),
                stock=_text(element, "[data-testid='stock']"),
            )
        )
    return rows


def _text(element: Any, selector: str) -> str:
    found = element.query_selector(selector)
    return (found.inner_text() or "").strip() if found else ""


def run_scenario(
    page: "Page",
    url: str,
    *,
    ready_selector: str = "[data-widget='productsList']",
    policy: RetryPolicy | None = None,
    artifacts_dir: str | Path = "artifacts",
) -> list[ProductRow]:
    """Целевое действие целиком: открыть список товаров и собрать его."""
    open_page(page, url, ready_selector, policy=policy, artifacts_dir=artifacts_dir)
    human_pause(policy)
    guard_antibot(page, artifacts_dir=artifacts_dir)
    products = collect_products(page)
    log.info("собрано товаров: %s", len(products))
    return products
