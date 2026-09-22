"""Пайплайн «Sheets -> pandas -> Sheets»: лист как витрина, расчёты снаружи.

Порядок: забрали сырьё одним запросом -> посчитали в pandas -> положили снимок
в Parquet (он же архив и вход для следующих прогонов) -> вернули в таблицу
готовые значения одним пакетом.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ozon_test.sheets.gsheets import SheetsClient
from ozon_test.sheets.optimize import (
    OptimizeReport,
    aggregate_by_article,
    deduplicate,
    normalize,
    save_compact,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    mart: pd.DataFrame
    report: OptimizeReport
    rows_written: int
    snapshot: Path


def sync_mart(
    client: SheetsClient,
    *,
    raw_range: str,
    mart_range: str,
    snapshot_path: str | Path = "data/mart.parquet",
) -> SyncResult:
    """Пересобрать витрину артикулов из сырого листа."""
    import time

    started = time.perf_counter()

    raw = client.read_dataframe(raw_range)
    log.info("прочитано строк из %s: %s", raw_range, len(raw))

    normalized = normalize(raw)
    deduped = deduplicate(normalized)
    mart = aggregate_by_article(deduped)

    snapshot = save_compact(mart, snapshot_path)
    rows_written = client.write_dataframe(mart_range, mart)
    log.info("записано строк в %s: %s", mart_range, rows_written)

    report = OptimizeReport(
        rows_in=len(raw),
        rows_dropped=len(raw) - len(normalized),
        rows_deduplicated=len(normalized) - len(deduped),
        rows_out=len(mart),
        # Оценка объёма сырья в листе: ~10 байт на ячейку — считать точнее незачем,
        # число нужно только для лога.
        bytes_in=len(raw) * max(1, len(raw.columns)) * 10,
        bytes_out=snapshot.stat().st_size,
        elapsed_seconds=time.perf_counter() - started,
    )
    return SyncResult(mart=mart, report=report, rows_written=rows_written, snapshot=snapshot)
