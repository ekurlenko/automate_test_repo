from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from sheets.models import Report
from sheets.settings import KEY, MART, MONEY, SCHEMA


def _numeric(series: pd.Series) -> pd.Series:
    """«1 234,50» и пустая строка — тоже данные."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    text = (series.astype("string")
            .str.replace(" ", "", regex=False)
            .str.replace(" ", "", regex=False)
            .str.replace(",", ".", regex=False))
    return pd.to_numeric(text, errors="coerce")


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Привести к SCHEMA и выкинуть строки без ключа."""
    missing = set(SCHEMA) - set(df.columns)
    if missing:
        raise ValueError(f"в выгрузке нет колонок: {sorted(missing)}")

    out = df.loc[:, list(SCHEMA)].copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce", format="mixed")
    for column in ("article", "warehouse"):
        out[column] = out[column].astype("string").str.strip().replace("", pd.NA)
    for column in MONEY:
        out[column] = _numeric(out[column]).fillna(0)

    out = out.dropna(subset=["date", "article"]).reset_index(drop=True)
    out = out.astype({"qty": "int32", "price": "float32", "revenue": "float32"})
    for column in ("article", "warehouse"):
        out[column] = out[column].fillna("—").astype("category")
    return out


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Оставить последнюю версию строки: выгрузки дописываются в конец."""
    return df.drop_duplicates(subset=list(KEY), keep="last").reset_index(drop=True)


def by_article(df: pd.DataFrame) -> pd.DataFrame:
    """Свернуть продажи по артикулу — это и есть витрина."""
    if df.empty:
        return pd.DataFrame(columns=list(MART))

    # observed=True: иначе category даст декартово произведение категорий.
    mart = (df.groupby("article", observed=True)
            .agg(qty=("qty", "sum"), revenue=("revenue", "sum"),
                 days_with_sales=("date", "nunique"),
                 first_sale=("date", "min"), last_sale=("date", "max"))
            .reset_index())
    # Выручка / штуки: среднее по price не взвешено по количеству.
    mart["avg_price"] = (mart["revenue"] / mart["qty"].where(mart["qty"] != 0)).fillna(0)
    mart["article"] = mart["article"].astype("string")
    return mart.loc[:, list(MART)].sort_values("revenue", ascending=False).reset_index(drop=True)


def load(source: str | Path) -> pd.DataFrame:
    """Прочитать сырьё."""
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


def to_parquet(df: pd.DataFrame, dest: str | Path) -> Path:
    """Рабочий формат: сжатие и типы внутри файла."""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    return path


def to_csv(df: pd.DataFrame, dest: str | Path) -> Path:
    """CSV под импорт в Sheets: даты без времени, числа с точкой, BOM."""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[column]):
            out[column] = out[column].dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, encoding="utf-8-sig", float_format="%.2f")
    return path


def build_mart(
    source: str | Path, parquet: str | Path, csv: str | Path | None = None
) -> tuple[pd.DataFrame, Report]:
    """Сырой файл -> чистка -> дедупликация -> витрина -> Parquet (и CSV)."""
    started = time.perf_counter()
    raw = load(source)
    cleaned = clean(raw)
    deduped = dedupe(cleaned)
    mart = by_article(deduped)

    dest = to_parquet(mart, parquet)
    if csv:
        to_csv(mart, csv)
    return mart, Report(
        rows_in=len(raw),
        rows_dropped=len(raw) - len(cleaned),
        rows_deduped=len(cleaned) - len(deduped),
        rows_out=len(mart),
        bytes_in=Path(source).stat().st_size,
        bytes_out=dest.stat().st_size,
        seconds=time.perf_counter() - started,
    )
