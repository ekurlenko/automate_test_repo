from __future__ import annotations


class AdsPowerError(Exception):
    """Базовая ошибка работы с Local API."""


class ApiDown(AdsPowerError):
    """Local API не отвечает: AdsPower закрыт или сменил порт. Ретраим."""


class ApiRejected(AdsPowerError):
    """API ответило с code != 0. Ретраить бессмысленно — ответ не изменится."""

    def __init__(self, code: int, message: str):
        super().__init__(f"AdsPower вернул code={code}: {message}")
        self.code = code


class NoDebugPort(AdsPowerError):
    """Профиль запустился, но подключаться некуда."""
