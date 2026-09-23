from __future__ import annotations

from config import DATA_DIR, env

# Явные типы — половина экономии памяти: category вместо object.
SCHEMA = {"date": "datetime64[ns]", "article": "category", "warehouse": "category",
          "qty": "int32", "price": "float32", "revenue": "float32"}
# Одна продажа = артикул + склад + день; повтор по ключу — правка.
KEY = ("date", "article", "warehouse")
MONEY = ("qty", "price", "revenue")
MART = ("article", "qty", "revenue", "avg_price", "days_with_sales",
        "first_sale", "last_sale")

RAW_FILE = DATA_DIR / "raw_sales.csv"
CSV_FILE = DATA_DIR / "mart.csv"

WAREHOUSES = ("Хоругвино", "Тверь", "Софьино", "Казань", "Екатеринбург")

RAW_RANGE = env("RAW_RANGE", "raw!A:F")
MART_RANGE = env("MART_RANGE", "mart!A1")

# Реальный лимит выше, но крупные запросы чаще ловят таймаут шлюза, чем проходят.
CELLS_PER_CALL = 100_000
# Sheets отдаёт эти коды при перегрузе; остальное — наша ошибка, её не ретраим.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
SHEETS_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
