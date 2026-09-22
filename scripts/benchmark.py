"""Замер: наивная загрузка против оптимизированной.

    python scripts/benchmark.py --source data/raw_sales.csv
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from ozon_test.sheets.optimize import optimize_sales


def _mb(value: float) -> str:
    return f"{value / 1024 / 1024:.1f} МБ"


def naive_load(source: Path) -> tuple[float, int]:
    """Как обычно делают: read_csv без типов, всё в object."""
    started = time.perf_counter()
    df = pd.read_csv(source)
    return time.perf_counter() - started, int(df.memory_usage(deep=True).sum())


def optimized_load(source: Path) -> tuple[float, int]:
    from ozon_test.sheets.optimize import deduplicate, normalize, read_raw

    started = time.perf_counter()
    df = deduplicate(normalize(read_raw(source)))
    return time.perf_counter() - started, int(df.memory_usage(deep=True).sum())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/raw_sales.csv"))
    parser.add_argument("--dest", type=Path, default=Path("data/mart.parquet"))
    args = parser.parse_args()

    naive_time, naive_memory = naive_load(args.source)
    fast_time, fast_memory = optimized_load(args.source)
    mart, report = optimize_sales(args.source, args.dest)

    csv_dest = args.dest.with_suffix(".csv")
    mart.to_csv(csv_dest, index=False)

    print("=== память на сыром кадре ===")
    print(f"наивно (object):      {_mb(naive_memory)}  за {naive_time:.2f} с")
    print(f"с типами + дедуп:     {_mb(fast_memory)}  за {fast_time:.2f} с")
    print(f"экономия памяти:      в {naive_memory / fast_memory:.1f}x\n")

    print("=== полный пайплайн ===")
    print(report.as_text(), "\n")

    print("=== витрина на диске ===")
    print(f"CSV:     {_mb(csv_dest.stat().st_size)}")
    print(f"Parquet: {_mb(args.dest.stat().st_size)} "
          f"(в {csv_dest.stat().st_size / args.dest.stat().st_size:.1f}x компактнее)")


if __name__ == "__main__":
    main()
