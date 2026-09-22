"""Точка входа блока 2: пересобрать витрину в Google Sheets.

    python scripts/sync_sheet.py

Настройки берутся из .env (см. .env.example): ключ сервисного аккаунта,
ID таблицы, диапазоны сырья и витрины.
"""

from __future__ import annotations

import logging
import os
import sys

from ozon_test.sheets.gsheets import SheetsClient
from ozon_test.sheets.pipeline import sync_mart


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if not spreadsheet_id or not credentials:
        print("нужны SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_FILE", file=sys.stderr)
        return 1

    client = SheetsClient.from_service_account(credentials, spreadsheet_id)
    result = sync_mart(
        client,
        raw_range=os.getenv("RAW_RANGE", "raw!A:F"),
        mart_range=os.getenv("MART_RANGE", "mart!A1"),
    )
    print(result.report.as_text())
    print(f"снимок витрины: {result.snapshot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
