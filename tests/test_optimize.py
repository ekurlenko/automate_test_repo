"""Тесты на блок 2: очистка, дедупликация и агрегация продаж."""

from __future__ import annotations

import pandas as pd
import pytest

from ozon_test.sheets.optimize import (
    RAW_DTYPES,
    aggregate_by_article,
    deduplicate,
    normalize,
    optimize_sales,
    save_compact,
)


def _raw(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(RAW_DTYPES))


def test_normalize_coerces_numbers_written_by_humans():
    """В выгрузках регулярно встречаются "1 234,50", пробелы и пустые ячейки."""
    df = normalize(
        _raw(
            [
                {"date": "2026-09-01", "article": " ART-1 ", "warehouse": "Хоругвино",
                 "qty": "3", "price": "1 234,50", "revenue": "3 703,50"},
                {"date": "2026-09-01", "article": "ART-2", "warehouse": "Тверь",
                 "qty": "", "price": "100", "revenue": "0"},
            ]
        )
    )

    assert df.loc[0, "article"] == "ART-1"
    assert df.loc[0, "price"] == pytest.approx(1234.50)
    assert df.loc[0, "revenue"] == pytest.approx(3703.50)
    assert df.loc[1, "qty"] == 0  # пустая ячейка -> 0, а не NaN и не падение
    assert str(df["article"].dtype) == "category"


def test_normalize_drops_rows_without_key():
    df = normalize(
        _raw(
            [
                {"date": "2026-09-01", "article": "ART-1", "warehouse": "Тверь",
                 "qty": 1, "price": 10, "revenue": 10},
                {"date": "не дата", "article": "ART-2", "warehouse": "Тверь",
                 "qty": 1, "price": 10, "revenue": 10},
                {"date": "2026-09-02", "article": "", "warehouse": "Тверь",
                 "qty": 1, "price": 10, "revenue": 10},
            ]
        )
    )

    assert len(df) == 1
    assert df.loc[0, "article"] == "ART-1"


def test_deduplicate_keeps_last_version_of_the_same_key():
    """Строка по тому же артикулу/дате/складу — это правка, а не второй заказ."""
    df = normalize(
        _raw(
            [
                {"date": "2026-09-01", "article": "ART-1", "warehouse": "Тверь",
                 "qty": 5, "price": 100, "revenue": 500},
                {"date": "2026-09-01", "article": "ART-1", "warehouse": "Тверь",
                 "qty": 7, "price": 100, "revenue": 700},
                {"date": "2026-09-01", "article": "ART-1", "warehouse": "Хоругвино",
                 "qty": 2, "price": 100, "revenue": 200},
            ]
        )
    )

    deduped = deduplicate(df)

    assert len(deduped) == 2
    tver = deduped[deduped["warehouse"] == "Тверь"].iloc[0]
    assert tver["qty"] == 7  # осталась последняя версия, а не первая


def test_aggregate_by_article_sums_sales_and_keeps_period():
    df = normalize(
        _raw(
            [
                {"date": "2026-09-01", "article": "ART-1", "warehouse": "Тверь",
                 "qty": 3, "price": 100, "revenue": 300},
                {"date": "2026-09-05", "article": "ART-1", "warehouse": "Хоругвино",
                 "qty": 2, "price": 120, "revenue": 240},
                {"date": "2026-09-03", "article": "ART-2", "warehouse": "Тверь",
                 "qty": 1, "price": 50, "revenue": 50},
            ]
        )
    )

    mart = aggregate_by_article(df)

    art1 = mart[mart["article"] == "ART-1"].iloc[0]
    assert art1["qty"] == 5
    assert art1["revenue"] == pytest.approx(540.0)
    assert art1["avg_price"] == pytest.approx(108.0)  # выручка / штуки, а не среднее цен
    assert str(art1["first_sale"].date()) == "2026-09-01"
    assert str(art1["last_sale"].date()) == "2026-09-05"
    assert art1["days_with_sales"] == 2
    assert list(mart["article"]) == ["ART-1", "ART-2"]  # отсортировано по выручке


def test_aggregate_returns_empty_frame_with_schema_on_empty_input():
    mart = aggregate_by_article(normalize(_raw([])))

    assert mart.empty
    assert list(mart.columns) == [
        "article", "qty", "revenue", "avg_price",
        "days_with_sales", "first_sale", "last_sale",
    ]


def test_save_compact_roundtrips_without_losing_types(tmp_path):
    """Parquet хранит схему внутри файла — в отличие от CSV, где всё снова строки.

    Выигрыш по размеру у Parquet проявляется на объёме (у файла ~5 КБ метаданных),
    поэтому его меряет бенчмарк scripts/benchmark.py, а не юнит-тест.
    """
    df = normalize(
        _raw(
            [
                {"date": f"2026-09-{day:02d}", "article": f"ART-{day % 7}",
                 "warehouse": "Тверь", "qty": day, "price": 100, "revenue": day * 100}
                for day in range(1, 29)
            ]
        )
    )
    mart = aggregate_by_article(df)

    parquet = save_compact(mart, tmp_path / "mart.parquet")
    restored = pd.read_parquet(parquet)

    assert restored.equals(mart)
    assert restored.dtypes.to_dict() == mart.dtypes.to_dict()
    assert pd.api.types.is_datetime64_any_dtype(restored["first_sale"])


def test_optimize_sales_reports_what_it_did(tmp_path):
    source = tmp_path / "raw.csv"
    pd.DataFrame(
        [
            {"date": "2026-09-01", "article": "ART-1", "warehouse": "Тверь",
             "qty": 5, "price": 100, "revenue": 500},
            {"date": "2026-09-01", "article": "ART-1", "warehouse": "Тверь",
             "qty": 7, "price": 100, "revenue": 700},
            {"date": "2026-09-02", "article": "ART-2", "warehouse": "Тверь",
             "qty": 1, "price": 50, "revenue": 50},
            {"date": "", "article": "ART-3", "warehouse": "Тверь",
             "qty": 1, "price": 50, "revenue": 50},
        ]
    ).to_csv(source, index=False)

    mart, report = optimize_sales(source, tmp_path / "mart.parquet")

    assert report.rows_in == 4
    assert report.rows_dropped == 1
    assert report.rows_deduplicated == 1
    assert report.rows_out == 2
    assert len(mart) == 2
    assert report.compression_ratio > 0
