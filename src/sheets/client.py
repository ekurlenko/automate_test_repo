"""Google Sheets: читаем и пишем пакетами, пишем значения, а не формулы."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import pandas as pd
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from sheets.mart import by_article, clean, dedupe, to_csv
from sheets.models import Report, Synced
from sheets.settings import CELLS_PER_CALL, CSV_FILE, RETRY_STATUS, SHEETS_SCOPES

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)


class Values(Protocol):
    """`service.spreadsheets().values()`: протокол, чтобы тестировать без сети."""

    def get(self, **kwargs: Any) -> Any: ...
    def update(self, **kwargs: Any) -> Any: ...
    def clear(self, **kwargs: Any) -> Any: ...


def _retryable(error: BaseException) -> bool:
    return getattr(getattr(error, "resp", None), "status", None) in RETRY_STATUS


_with_retry = retry(
    retry=retry_if_exception(_retryable),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)


@dataclass
class Sheet:
    """Доступ к одной таблице: пакетные чтение и запись, значения без формул."""

    spreadsheet_id: str
    values: Values

    @classmethod
    def open(cls, key_file: str, spreadsheet_id: str) -> Sheet:
        """Собрать клиент по ключу сервисного аккаунта."""
        # Импорты внутри: google-библиотеки нужны только для реального доступа.
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials.from_service_account_file(
            key_file, scopes=list(SHEETS_SCOPES)
        )
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return cls(spreadsheet_id, service.spreadsheets().values())

    @_with_retry
    def read(self, cells: str) -> list[list[str]]:
        """Забрать диапазон одним запросом; UNFORMATTED_VALUE даёт результат формул."""
        response = self.values.get(
            spreadsheetId=self.spreadsheet_id, range=cells,
            valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="FORMATTED_STRING",
        ).execute() or {}
        return response.get("values", [])

    def frame(self, cells: str) -> pd.DataFrame:
        """Диапазон как таблица: первая строка — заголовки."""
        rows = self.read(cells)
        if not rows:
            return pd.DataFrame()
        header, *body = rows
        width = len(header)
        # Sheets обрезает хвостовые пустые ячейки: строки разной длины.
        return pd.DataFrame([row[:width] + [""] * (width - len(row)) for row in body],
                            columns=header, dtype="string")

    @_with_retry
    def _clear(self, cells: str) -> None:
        self.values.clear(spreadsheetId=self.spreadsheet_id, range=cells, body={}).execute()

    @_with_retry
    def _update(self, cells: str, chunk: list[list[Any]]) -> None:
        # RAW: USER_ENTERED превратил бы строки вида "=..." в формулы.
        self.values.update(spreadsheetId=self.spreadsheet_id, range=cells,
                           valueInputOption="RAW", body={"values": chunk}).execute()

    def write(self, cells: str, df: pd.DataFrame, *, clear_first: bool = True) -> int:
        """Выложить витрину пакетами; чистка нужна, иначе внизу останется хвост."""
        if clear_first:
            self._clear(cells)
        if df.empty:
            return 0

        payload = [list(df.columns), *_cells(df)]
        sheet, _, anchor = cells.partition("!")
        first_row = int("".join(c for c in anchor if c.isdigit()) or 1)
        per_chunk = max(1, CELLS_PER_CALL // max(1, len(df.columns)))

        written = 0
        for offset in range(0, len(payload), per_chunk):
            chunk = payload[offset : offset + per_chunk]
            self._update(f"{sheet}!A{first_row + offset}" if sheet else cells, chunk)
            written += len(chunk)
        return written - 1  # заголовок не строка данных


def _cells(df: pd.DataFrame) -> list[list[Any]]:
    """В JSON-совместимые значения: Timestamp и NaN сериализатор не знает."""
    out = df.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[column]):
            out[column] = out[column].dt.strftime("%Y-%m-%d")
    return out.astype(object).where(out.notna(), "").to_numpy().tolist()


def sync(
    sheet: Sheet,
    *,
    raw_cells: str,
    mart_cells: str,
    csv: str | Path = CSV_FILE,
) -> Synced:
    """Пересобрать витрину: один get, расчёт в pandas, один update."""
    started = time.perf_counter()
    raw = sheet.frame(raw_cells)
    log.info("прочитано строк из %s: %s", raw_cells, len(raw))

    cleaned = clean(raw)
    deduped = dedupe(cleaned)
    mart = by_article(deduped)

    csv_path = to_csv(mart, csv)
    rows_written = sheet.write(mart_cells, mart)
    log.info("записано строк в %s: %s", mart_cells, rows_written)

    return Synced(mart, Report(
        rows_in=len(raw),
        rows_dropped=len(raw) - len(cleaned),
        rows_deduped=len(cleaned) - len(deduped),
        rows_out=len(mart),
        bytes_in=len(raw) * max(1, len(raw.columns)) * 10,  # ~10 байт на ячейку
        bytes_out=csv_path.stat().st_size,
        seconds=time.perf_counter() - started,
    ), rows_written, csv_path)
