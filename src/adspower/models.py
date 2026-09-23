from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    user_id: str
    cdp_url: str       # ws:// для Playwright и Puppeteer
    selenium: str      # host:port для Selenium
    debug_port: str    # порт Chrome DevTools Protocol
    webdriver: str     # путь к chromedriver нужной версии
