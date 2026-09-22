"""Сгенерировать сырую выгрузку, похожую на реальную: с дублями и грязью.

    python scripts/generate_sample.py --rows 200000 --out data/raw_sales.csv
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import pandas as pd

WAREHOUSES = ["Хоругвино", "Тверь", "Софьино", "Казань", "Екатеринбург"]


def generate(rows: int, articles: int, seed: int = 42) -> pd.DataFrame:
    random.seed(seed)
    dates = pd.date_range("2026-01-01", "2026-09-21", freq="D")

    records = []
    for _ in range(rows):
        qty = random.randint(1, 12)
        price = round(random.uniform(150, 9000), 2)
        records.append(
            {
                "date": random.choice(dates).strftime("%Y-%m-%d"),
                "article": f"ART-{random.randrange(articles):06d}",
                "warehouse": random.choice(WAREHOUSES),
                "qty": qty,
                # Цены в выгрузках приходят по-человечески: пробел-разделитель и запятая.
                "price": f"{price:,.2f}".replace(",", " ").replace(".", ","),
                "revenue": round(qty * price, 2),
            }
        )

    df = pd.DataFrame(records)

    # ~8% дублей: типичный след повторных выгрузок и правок задним числом.
    duplicates = df.sample(frac=0.08, random_state=seed)
    # ~1% мусора: строки без даты или без артикула.
    broken = df.sample(frac=0.01, random_state=seed + 1).copy()
    broken.loc[broken.index[::2], "date"] = ""
    broken.loc[broken.index[1::2], "article"] = ""

    return pd.concat([df, duplicates, broken], ignore_index=True).sample(
        frac=1, random_state=seed
    ).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--articles", type=int, default=4_000)
    parser.add_argument("--out", type=Path, default=Path("data/raw_sales.csv"))
    args = parser.parse_args()

    df = generate(args.rows, args.articles)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"записано {len(df):,} строк в {args.out} "
          f"({args.out.stat().st_size / 1024 / 1024:.1f} МБ)".replace(",", " "))


if __name__ == "__main__":
    main()
