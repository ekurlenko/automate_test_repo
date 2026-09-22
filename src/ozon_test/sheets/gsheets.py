"""Тонкий клиент Google Sheets API.

Два правила, из-за нарушения которых таблицы и скрипты обычно и тормозят:

1. Читаем и пишем ПАКЕТАМИ. Один `values.get` на весь диапазон вместо тысячи
   обращений по ячейке; один `values.update` на всю витрину вместо построчного
   append. Квота Sheets API — 60 запросов в минуту на пользователя, построчная
   запись упирается в неё на первой же сотне строк.
2. Пишем ЗНАЧЕНИЯ, а не формулы (`valueInputOption=RAW`). Лист, в котором нет
   формул, нечего пересчитывать — он открывается мгновенно независимо от объёма.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import pandas as pd
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

# Sheets API отдаёт эти коды при перегрузе; всё остальное — наша ошибка, её не ретраим.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Потолок ячеек на один запрос. Реальный лимит выше, но крупные запросы чаще
# ловят таймаут шлюза, чем проходят.
MAX_CELLS_PER_REQUEST = 100_000


class ValuesResource(Protocol):
    """Интерфейс `service.spreadsheets().values()` — ровно то, что мы используем.

    Зависимость объявлена протоколом, а не классом googleapiclient: так клиент
    тестируется подставным объектом, без сети и без сервисного аккаунта.
    """

    def get(self, **kwargs: Any) -> Any: ...
    def update(self, **kwargs: Any) -> Any: ...
    def clear(self, **kwargs: Any) -> Any: ...


def _is_retryable(error: BaseException) -> bool:
    status = getattr(getattr(error, "resp", None), "status", None)
    return status in RETRYABLE_STATUS


_with_retry = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)


@dataclass
class SheetsClient:
    """Доступ к одной таблице."""

    spreadsheet_id: str
    values: ValuesResource

    @classmethod
    def from_service_account(cls, credentials_file: str, spreadsheet_id: str) -> SheetsClient:
        """Собрать клиент по ключу сервисного аккаунта.

        Импорты внутри метода намеренно: сам клиент и его тесты не требуют
        установленных google-библиотек, они нужны только для реального доступа.
        """
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials.from_service_account_file(
            credentials_file,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return cls(spreadsheet_id=spreadsheet_id, values=service.spreadsheets().values())

    @_with_retry
    def read_values(self, range_name: str) -> list[list[str]]:
        """Забрать диапазон одним запросом.

        UNFORMATTED_VALUE просит у API результат формул, а не их текст: даже если
        в листе ещё остались формулы, на вход pandas придут числа.
        """
        response = (
            self.values.get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING",
            ).execute()
            or {}
        )
        return response.get("values", [])

    def read_dataframe(self, range_name: str) -> pd.DataFrame:
        """Прочитать диапазон как таблицу: первая строка — заголовки."""
        rows = self.read_values(range_name)
        if not rows:
            return pd.DataFrame()

        header, *body = rows
        width = len(header)
        # Sheets обрезает хвостовые пустые ячейки, строки приходят разной длины.
        padded = [row[:width] + [""] * (width - len(row)) for row in body]
        return pd.DataFrame(padded, columns=header, dtype="string")

    @_with_retry
    def _clear(self, range_name: str) -> None:
        self.values.clear(spreadsheetId=self.spreadsheet_id, range=range_name, body={}).execute()

    @_with_retry
    def _update(self, range_name: str, chunk: list[list[Any]]) -> None:
        self.values.update(
            spreadsheetId=self.spreadsheet_id,
            range=range_name,
            # RAW: записываем значения как есть. USER_ENTERED заставил бы Sheets
            # разбирать каждую ячейку и превращать строки вида "=..." в формулы.
            valueInputOption="RAW",
            body={"values": chunk},
        ).execute()

    def write_dataframe(self, range_name: str, df: pd.DataFrame, *, clear_first: bool = True) -> int:
        """Выложить витрину в лист пакетами. Возвращает число записанных строк.

        clear_first обязателен, когда витрина может сократиться: иначе внизу
        останется хвост прошлой выгрузки.
        """
        if clear_first:
            self._clear(range_name)
        if df.empty:
            return 0

        payload = [list(df.columns)] + _to_cells(df)
        sheet, _, anchor = range_name.partition("!")
        start_row = _anchor_row(anchor)
        rows_per_chunk = max(1, MAX_CELLS_PER_REQUEST // max(1, len(df.columns)))

        written = 0
        for offset in range(0, len(payload), rows_per_chunk):
            chunk = payload[offset : offset + rows_per_chunk]
            target = f"{sheet}!A{start_row + offset}" if sheet else range_name
            self._update(target, chunk)
            written += len(chunk)
        return written - 1  # заголовок не считаем строкой данных


def _to_cells(df: pd.DataFrame) -> list[list[Any]]:
    """Превратить кадр в JSON-совместимые значения.

    Даты — в ISO-строки, NaN — в пустую строку: JSON-сериализатор не знает ни
    Timestamp, ни NaN, и падает на них уже внутри googleapiclient.
    """
    prepared = df.copy()
    for column in prepared.columns:
        if pd.api.types.is_datetime64_any_dtype(prepared[column]):
            prepared[column] = prepared[column].dt.strftime("%Y-%m-%d")
    return prepared.astype(object).where(prepared.notna(), "").values.tolist()


def _anchor_row(anchor: str) -> int:
    """Вытащить номер строки из якоря диапазона: "A1" -> 1, "mart" -> 1."""
    digits = "".join(ch for ch in anchor if ch.isdigit())
    return int(digits) if digits else 1
