from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

import pandas as pd

from config import GOOGLE_SERVICE_ACCOUNT_FILE, SPREADSHEET_ID, setup_logging
from sheets.client import Sheet, sync
from sheets.mart import build_mart, clean, dedupe, load
from sheets.settings import (
    CSV_FILE,
    MART_RANGE,
    RAW_FILE,
    RAW_RANGE,
    WAREHOUSES,
)

log = logging.getLogger(__name__)


def sample(rows: int, articles: int = 4000, seed: int = 42) -> pd.DataFrame:
    """Сырьё, похожее на реальную выгрузку: дубли, битые строки, «1 234,50»."""
    random.seed(seed)
    dates = pd.date_range("2026-01-01", "2026-09-21", freq="D")
    records = []
    for _ in range(rows):
        qty = random.randint(1, 12)  # noqa: S311
        price = round(random.uniform(150, 9000), 2)  # noqa: S311
        records.append({"date": random.choice(dates).strftime("%Y-%m-%d"),  # noqa: S311
                        "article": f"ART-{random.randrange(articles):06d}",  # noqa: S311
                        "warehouse": random.choice(WAREHOUSES),  # noqa: S311
                        # Цены в выгрузках приходят по-человечески: пробел и запятая.
                        "qty": qty, "price": f"{price:,.2f}".replace(",", " ").replace(".", ","),
                        "revenue": round(qty * price, 2)})
    clean_rows = pd.DataFrame(records)
    duplicates = clean_rows.sample(frac=0.08, random_state=seed)  # след повторных выгрузок
    broken = clean_rows.sample(frac=0.01, random_state=seed + 1).copy()
    broken.loc[broken.index[::2], "date"] = ""
    broken.loc[broken.index[1::2], "article"] = ""
    return (pd.concat([clean_rows, duplicates, broken], ignore_index=True)
            .sample(frac=1, random_state=seed).reset_index(drop=True))


def _mb(value: float) -> str:
    return f"{value / 1024 / 1024:.1f} МБ"


def _bench(source: Path, csv: Path) -> None:
    started = time.perf_counter()
    naive_memory = int(pd.read_csv(source).memory_usage(deep=True).sum())
    naive_time = time.perf_counter() - started

    started = time.perf_counter()
    fast_memory = int(dedupe(clean(load(source))).memory_usage(deep=True).sum())
    fast_time = time.perf_counter() - started

    _mart, report = build_mart(source, csv)

    print("=== память на сыром кадре ===")
    print(f"наивно (object):      {_mb(naive_memory)}  за {naive_time:.2f} с")
    print(f"с типами + дедуп:     {_mb(fast_memory)}  за {fast_time:.2f} с")
    print(f"экономия памяти:      в {naive_memory / fast_memory:.1f}x\n")
    print("=== полный пайплайн ===")
    print(report.text(), "\n")
    print("=== витрина на диске ===")
    print(f"{csv}: {_mb(csv.stat().st_size)}")


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Витрина продаж: pandas вместо формул")
    parser.add_argument("--raw", type=Path, default=RAW_FILE)
    parser.add_argument("--csv", type=Path, default=CSV_FILE)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("sample", help="сгенерировать сырьё")
    generate.add_argument("--rows", type=int, default=200_000)
    generate.add_argument("--articles", type=int, default=4_000)
    commands.add_parser("bench", help="замеры: наивно против витрины")
    commands.add_parser("sync", help="Google Sheets -> витрина -> Google Sheets")
    args = parser.parse_args(argv)

    match args.command:
        case "sample":
            args.raw.parent.mkdir(parents=True, exist_ok=True)
            sample(args.rows, args.articles).to_csv(args.raw, index=False)
            print(f"{args.raw}: {args.raw.stat().st_size / 1024 / 1024:.1f} МБ")
        case "bench":
            _bench(args.raw, args.csv)
        case "sync":
            spreadsheet_id, key_file = SPREADSHEET_ID, GOOGLE_SERVICE_ACCOUNT_FILE
            if not spreadsheet_id or not key_file:
                print("нужны SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_FILE", file=sys.stderr)
                return 1
            result = sync(
                Sheet.open(key_file, spreadsheet_id),
                raw_cells=RAW_RANGE,
                mart_cells=MART_RANGE,
                csv=args.csv,
            )
            print(result.report.text())
            print(f"CSV для импорта: {result.csv}")
    return 0
