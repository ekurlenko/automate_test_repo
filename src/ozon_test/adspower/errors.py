"""Ошибки блока 1.

Разделены по уровням не для красоты: на каждом уровне своя реакция.
Транспорт — ретраим. Страницу — перезагружаем. Антибот — отступаем и меняем IP.
"""

from __future__ import annotations


class AdsPowerError(Exception):
    """Базовая ошибка работы с AdsPower."""


class LocalApiUnavailable(AdsPowerError):
    """Local API не отвечает: приложение AdsPower закрыто или сменило порт."""


class ApiRejected(AdsPowerError):
    """API ответило, но с code != 0."""

    def __init__(self, code: int, message: str):
        super().__init__(f"AdsPower вернул code={code}: {message}")
        self.code = code
        self.message = message


class ProfileStartError(AdsPowerError):
    """Профиль не запустился или запустился без точки отладки."""


class PageStalled(AdsPowerError):
    """Страница загрузилась, но целевой контент так и не появился."""


class CaptchaDetected(AdsPowerError):
    """Замечена капча или страница блокировки.

    Отдельный тип намеренно: это не повод для ретрая в лоб. Повторные попытки
    с того же IP только укрепляют подозрение антифрода.
    """

    def __init__(self, url: str, screenshot: str | None = None):
        super().__init__(f"капча или блокировка на {url}")
        self.url = url
        self.screenshot = screenshot
