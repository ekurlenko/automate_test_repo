"""Очистка и агрегация сырой выгрузки продаж.

Идея блока 2: таблица — это витрина, а не база данных. Сырьё живёт в файле
(Parquet), все расчёты делает pandas, в Google Sheets уезжают только готовые
значения. Ни одной формулы в листе — пересчитывать нечего, тормозить нечему.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Схема сырой выгрузки. Явные типы — половина экономии памяти:
# артикул как category вместо object даёт кратное падение на десятках тысяч строк.
RAW_DTYPES: dict[str, str] = {
    "date": "datetime64[ns]",
    "article": "category",
    "warehouse": "category",
    "qty": "int32",
    "price": "float32",
    "revenue": "float32",
}

# Ключ строки: одна продажа = артикул + склад + день.
# Повтор по этому ключу — это правка старой строки, а не вторая продажа.
KEY_COLUMNS: tuple[str, ...] = ("date", "article", "warehouse")

NUMERIC_COLUMNS: tuple[str, ...] = ("qty", "price", "revenue")

MART_COLUMNS: tuple[str, ...] = (
    "article",
    "qty",
    "revenue",
    "avg_price",
    "days_with_sales",
    "first_sale",
    "last_sale",
)


@dataclass(frozen=True)
class OptimizeReport:
    """Что именно сделал пайплайн — чтобы это можно было залогировать и показать."""

    rows_in: int
    rows_dropped: int
    rows_deduplicated: int
    rows_out: int
    bytes_in: int
    bytes_out: int
    elapsed_seconds: float

    @property
    def compression_ratio(self) -> float:
        """Во сколько раз результат компактнее исходника."""
        return self.bytes_in / self.bytes_out if self.bytes_out else 0.0

    def as_text(self) -> str:
        return (
            f"строк на входе: {self.rows_in:,}\n"
            f"отброшено битых: {self.rows_dropped:,}\n"
            f"снято дублей:    {self.rows_deduplicated:,}\n"
            f"строк в витрине: {self.rows_out:,}\n"
            f"объём: {self.bytes_in / 1024 / 1024:.2f} МБ -> "
            f"{self.bytes_out / 1024 / 1024:.2f} МБ "
            f"(в {self.compression_ratio:.1f}x компактнее)\n"
            f"время: {self.elapsed_seconds:.2f} с"
        ).replace(",", " ")


def _to_number(series: pd.Series) -> pd.Series:
    """Привести человеческий ввод к числу: "1 234,50" и "" тоже данные."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype("string")
        .str.replace(" ", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Привести сырые данные к схеме RAW_DTYPES и выкинуть строки без ключа.

    Строка без даты или без артикула неинтерпретируема — агрегировать её некуда,
    поэтому она отбрасывается явно, а не превращается в NaN-группу.
    """
    missing = set(RAW_DTYPES) - set(df.columns)
    if missing:
        raise ValueError(f"в выгрузке нет обязательных колонок: {sorted(missing)}")

    out = df.loc[:, list(RAW_DTYPES)].copy()

    out["date"] = pd.to_datetime(out["date"], errors="coerce", format="mixed")
    for column in ("article", "warehouse"):
        out[column] = out[column].astype("string").str.strip().replace("", pd.NA)
    for column in NUMERIC_COLUMNS:
        out[column] = _to_number(out[column]).fillna(0)

    out = out.dropna(subset=["date", "article"]).reset_index(drop=True)

    out["qty"] = out["qty"].astype("int32")
    out["price"] = out["price"].astype("float32")
    out["revenue"] = out["revenue"].astype("float32")
    for column in ("article", "warehouse"):
        out[column] = out[column].fillna("—").astype("category")

    return out


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Снять дубли по ключу, оставив последнюю версию строки.

    keep="last" — не вкусовщина: выгрузки дописываются в конец, поэтому нижняя
    строка по тому же ключу всегда свежее верхней.
    """
    return df.drop_duplicates(subset=list(KEY_COLUMNS), keep="last").reset_index(drop=True)


def aggregate_by_article(df: pd.DataFrame) -> pd.DataFrame:
    """Свернуть продажи по артикулу — это и есть витрина для таблицы."""
    if df.empty:
        return pd.DataFrame(columns=list(MART_COLUMNS))

    mart = (
        # observed=True обязателен: без него category разворачивается в декартово
        # произведение всех известных категорий и витрина распухает на пустых строках.
        df.groupby("article", observed=True)
        .agg(
            qty=("qty", "sum"),
            revenue=("revenue", "sum"),
            days_with_sales=("date", "nunique"),
            first_sale=("date", "min"),
            last_sale=("date", "max"),
        )
        .reset_index()
    )

    # Средняя цена реализации = выручка / штуки. Среднее по колонке price дало бы
    # неверный ответ: оно не взвешено по количеству.
    mart["avg_price"] = (mart["revenue"] / mart["qty"].where(mart["qty"] != 0)).fillna(0)

    mart["article"] = mart["article"].astype("string")
    mart = mart.loc[:, list(MART_COLUMNS)]
    return mart.sort_values("revenue", ascending=False).reset_index(drop=True)


def read_raw(source: str | Path) -> pd.DataFrame:
    """Прочитать сырьё из файла. CSV/Excel — то, что обычно выгружают руками."""
    path = Path(source)
    match path.suffix.lower():
        case ".csv":
            return pd.read_csv(path, dtype="string", keep_default_na=False)
        case ".parquet":
            return pd.read_parquet(path)
        case ".xlsx" | ".xlsm":
            return pd.read_excel(path, dtype="string")
        case _:
            raise ValueError(f"неподдерживаемый формат: {path.suffix!r}")


def save_compact(df: pd.DataFrame, dest: str | Path) -> Path:
    """Сохранить витрину в Parquet со сжатием — компактно и с типами внутри."""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    return path


def optimize_sales(source: str | Path, dest: str | Path) -> tuple[pd.DataFrame, OptimizeReport]:
    """Полный проход: сырой файл -> чистка -> дедупликация -> витрина -> Parquet."""
    started = time.perf_counter()
    source_path = Path(source)

    raw = read_raw(source_path)
    rows_in = len(raw)

    normalized = normalize(raw)
    rows_dropped = rows_in - len(normalized)

    deduped = deduplicate(normalized)
    rows_deduplicated = len(normalized) - len(deduped)

    mart = aggregate_by_article(deduped)
    dest_path = save_compact(mart, dest)

    report = OptimizeReport(
        rows_in=rows_in,
        rows_dropped=rows_dropped,
        rows_deduplicated=rows_deduplicated,
        rows_out=len(mart),
        bytes_in=source_path.stat().st_size,
        bytes_out=dest_path.stat().st_size,
        elapsed_seconds=time.perf_counter() - started,
    )
    return mart, report
