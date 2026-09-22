"""Тесты обработки ошибок на уровне страницы: без браузера, на двойнике Page."""

from __future__ import annotations

import pytest

from ozon_test.adspower.errors import CaptchaDetected, PageStalled
from ozon_test.adspower.scenario import (
    RetryPolicy,
    collect_products,
    looks_like_captcha,
    open_page,
    run_scenario,
)


class FakeElement:
    def __init__(self, fields: dict[str, str]):
        self._fields = fields

    def query_selector(self, selector: str):
        for key, value in self._fields.items():
            if key in selector:
                return FakeText(value)
        return None


class FakeText:
    def __init__(self, text: str):
        self._text = text

    def inner_text(self) -> str:
        return self._text


class FakePage:
    """Двойник Playwright Page: управляем тем, что «покажет» страница."""

    def __init__(
        self,
        *,
        url: str = "https://seller.ozon.ru/app/products",
        html: str = "<html>кабинет</html>",
        selectors: set[str] | None = None,
        fail_selector_times: int = 0,
        rows: list[dict] | None = None,
    ):
        self.url = url
        self.html = html
        self.selectors = selectors or set()
        self._fail_selector_times = fail_selector_times
        self.rows = rows or []
        self.goto_calls: list[str] = []
        self.reload_calls = 0
        self.screenshots: list[str] = []

    def goto(self, url: str, **kwargs):
        self.goto_calls.append(url)

    def reload(self, **kwargs):
        self.reload_calls += 1

    def content(self) -> str:
        return self.html

    def query_selector(self, selector: str):
        return object() if selector in self.selectors else None

    def query_selector_all(self, selector: str):
        return [FakeElement(row) for row in self.rows]

    def wait_for_selector(self, selector: str, **kwargs):
        if self._fail_selector_times > 0:
            self._fail_selector_times -= 1
            raise TimeoutError(f"селектор {selector} не появился")
        return object()

    def screenshot(self, path: str, **kwargs):
        self.screenshots.append(path)
        open(path, "wb").close()


def _policy(sleeps: list[float] | None = None, attempts: int = 3) -> RetryPolicy:
    recorded = [] if sleeps is None else sleeps
    return RetryPolicy(attempts=attempts, base_delay=1.0, sleep=recorded.append)


def test_captcha_is_detected_by_selector():
    page = FakePage(selectors={"#challenge-running"})

    assert looks_like_captcha(page) is True


def test_captcha_is_detected_by_url_even_without_known_markup():
    page = FakePage(url="https://seller.ozon.ru/challenge?ret=/app")

    assert looks_like_captcha(page) is True


def test_captcha_is_detected_by_page_text():
    page = FakePage(html="<h1>Доступ ограничен</h1>")

    assert looks_like_captcha(page) is True


def test_normal_page_is_not_mistaken_for_captcha():
    page = FakePage(html="<div data-widget='productsList'>Товары</div>")

    assert looks_like_captcha(page) is False


def test_open_page_reloads_a_stalled_page_and_succeeds(tmp_path):
    """Зависший SPA: DOM есть, контента нет. Лечится перезагрузкой."""
    sleeps: list[float] = []
    page = FakePage(fail_selector_times=1)

    open_page(page, "https://seller.ozon.ru/app/products", "[data-widget='productsList']",
              policy=_policy(sleeps), artifacts_dir=tmp_path)

    assert page.goto_calls == ["https://seller.ozon.ru/app/products"]
    assert page.reload_calls == 1  # второй заход — reload, а не повторный goto
    assert len(sleeps) == 1


def test_open_page_gives_up_with_page_stalled_and_saves_screenshot(tmp_path):
    page = FakePage(fail_selector_times=99)

    with pytest.raises(PageStalled, match="не появился"):
        open_page(page, "https://seller.ozon.ru/app/products", "[data-widget='productsList']",
                  policy=_policy(attempts=3), artifacts_dir=tmp_path)

    assert page.reload_calls == 2  # первая попытка goto + две перезагрузки
    assert len(page.screenshots) == 1


def test_open_page_backs_off_exponentially(tmp_path):
    sleeps: list[float] = []
    page = FakePage(fail_selector_times=99)

    with pytest.raises(PageStalled):
        open_page(page, "url", "sel", policy=_policy(sleeps, attempts=4), artifacts_dir=tmp_path)

    assert len(sleeps) == 3
    assert sleeps[0] < sleeps[1] < sleeps[2]  # паузы растут, с джиттером


def test_captcha_aborts_immediately_without_retries(tmp_path):
    """Капчу не ретраим: повторы с того же IP только усугубляют блок."""
    sleeps: list[float] = []
    page = FakePage(selectors={"#challenge-running"})

    with pytest.raises(CaptchaDetected) as exc:
        open_page(page, "https://seller.ozon.ru/app/products", "sel",
                  policy=_policy(sleeps), artifacts_dir=tmp_path)

    assert sleeps == []
    assert page.reload_calls == 0
    assert exc.value.screenshot is not None  # есть что разбирать потом


def test_collect_products_reads_rows_by_data_attributes():
    page = FakePage(
        rows=[
            {"article": "ART-1", "title": "Кружка", "price": "1 200 ₽", "stock": "14"},
            {"article": "ART-2", "title": "Плед", "price": "3 400 ₽", "stock": "0"},
        ]
    )

    products = collect_products(page)

    assert [p.article for p in products] == ["ART-1", "ART-2"]
    assert products[0].name == "Кружка"
    assert products[1].stock == "0"


def test_collect_products_survives_a_row_with_missing_cells():
    """Вёрстка кабинета меняется — отсутствующая ячейка не должна ронять сбор."""
    page = FakePage(rows=[{"article": "ART-1"}])

    products = collect_products(page)

    assert products[0].article == "ART-1"
    assert products[0].price == ""


def test_run_scenario_happy_path(tmp_path):
    page = FakePage(rows=[{"article": "ART-1", "title": "Кружка", "price": "1 200 ₽", "stock": "14"}])

    products = run_scenario(page, "https://seller.ozon.ru/app/products",
                            policy=_policy(), artifacts_dir=tmp_path)

    assert len(products) == 1
