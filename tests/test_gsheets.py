"""Тесты Sheets-клиента на подставном API: без сети и сервисного аккаунта."""

from __future__ import annotations

import pandas as pd
import pytest

from ozon_test.sheets.gsheets import MAX_CELLS_PER_REQUEST, SheetsClient
from ozon_test.sheets.pipeline import sync_mart


class FakeCall:
    def __init__(self, result, error: Exception | None = None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._result


class FakeResponse:
    def __init__(self, status: int):
        self.status = status


class FakeHttpError(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.resp = FakeResponse(status)


class FakeValues:
    """Минимальный двойник `service.spreadsheets().values()`."""

    def __init__(self, stored: list[list] | None = None, fail_times: int = 0, status: int = 429):
        self.stored = stored or []
        self.updates: list[dict] = []
        self.cleared: list[str] = []
        self._fail_times = fail_times
        self._status = status
        self.get_calls = 0

    def get(self, **kwargs):
        self.get_calls += 1
        if self._fail_times > 0:
            self._fail_times -= 1
            return FakeCall(None, FakeHttpError(self._status))
        return FakeCall({"values": self.stored})

    def update(self, **kwargs):
        self.updates.append(kwargs)
        return FakeCall({"updatedCells": len(kwargs["body"]["values"])})

    def clear(self, **kwargs):
        self.cleared.append(kwargs["range"])
        return FakeCall({})


def test_read_dataframe_pads_rows_truncated_by_sheets():
    """Sheets не отдаёт хвостовые пустые ячейки — строки приходят разной длины."""
    values = FakeValues([["date", "article", "qty"], ["2026-09-01", "ART-1", "3"], ["2026-09-02"]])
    client = SheetsClient("sheet-id", values)

    df = client.read_dataframe("raw!A:C")

    assert list(df.columns) == ["date", "article", "qty"]
    assert df.shape == (2, 3)
    assert df.loc[1, "article"] == ""


def test_read_dataframe_on_empty_sheet_returns_empty_frame():
    client = SheetsClient("sheet-id", FakeValues([]))

    assert client.read_dataframe("raw!A:C").empty


def test_read_values_retries_on_429_and_succeeds():
    values = FakeValues([["a"]], fail_times=2, status=429)
    client = SheetsClient("sheet-id", values)

    assert client.read_values("raw!A:A") == [["a"]]
    assert values.get_calls == 3


def test_read_values_does_not_retry_on_403():
    """Нет прав — ретраи бессмысленны, ошибку надо показать сразу."""
    values = FakeValues([["a"]], fail_times=5, status=403)
    client = SheetsClient("sheet-id", values)

    with pytest.raises(FakeHttpError):
        client.read_values("raw!A:A")
    assert values.get_calls == 1


def test_write_dataframe_clears_then_writes_values_not_formulas():
    values = FakeValues()
    client = SheetsClient("sheet-id", values)
    df = pd.DataFrame({"article": ["ART-1"], "revenue": [100.0], "first_sale": [pd.Timestamp("2026-09-01")]})

    written = client.write_dataframe("mart!A1", df)

    assert written == 1
    assert values.cleared == ["mart!A1"]
    assert values.updates[0]["valueInputOption"] == "RAW"
    body = values.updates[0]["body"]["values"]
    assert body[0] == ["article", "revenue", "first_sale"]
    assert body[1] == ["ART-1", 100.0, "2026-09-01"]  # Timestamp сериализован


def test_write_dataframe_splits_large_payload_into_chunks():
    values = FakeValues()
    client = SheetsClient("sheet-id", values)
    rows = MAX_CELLS_PER_REQUEST // 2 + 10  # две колонки -> заведомо больше одного пакета
    df = pd.DataFrame({"article": [f"ART-{i}" for i in range(rows)], "qty": range(rows)})

    written = client.write_dataframe("mart!A1", df)

    assert written == rows
    assert len(values.updates) > 1
    assert values.updates[1]["range"].startswith("mart!A")


def test_write_dataframe_of_empty_mart_only_clears():
    values = FakeValues()
    client = SheetsClient("sheet-id", values)

    assert client.write_dataframe("mart!A1", pd.DataFrame()) == 0
    assert values.cleared == ["mart!A1"]
    assert values.updates == []


def test_sync_mart_reads_once_and_writes_once(tmp_path):
    """Весь проход — один get и один update, а не запрос на строку."""
    values = FakeValues(
        [
            ["date", "article", "warehouse", "qty", "price", "revenue"],
            ["2026-09-01", "ART-1", "Тверь", "5", "100", "500"],
            ["2026-09-01", "ART-1", "Тверь", "7", "100", "700"],
            ["2026-09-02", "ART-2", "Тверь", "1", "50", "50"],
        ]
    )
    client = SheetsClient("sheet-id", values)

    result = sync_mart(
        client,
        raw_range="raw!A:F",
        mart_range="mart!A1",
        snapshot_path=tmp_path / "mart.parquet",
    )

    assert values.get_calls == 1
    assert len(values.updates) == 1
    assert result.report.rows_deduplicated == 1
    assert result.rows_written == 2
    assert result.snapshot.exists()
    assert result.mart.iloc[0]["revenue"] == pytest.approx(700.0)
