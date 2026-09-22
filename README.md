# Ozon + AdsPower: тестовое задание

Ответ на тестовое задание целиком: **[docs/ANSWER.md](docs/ANSWER.md)**.

Репозиторий — не иллюстрация к документу, а работающий код с тестами.

## Что здесь

**Блок 1** — автоматизация кабинета Ozon через антидетект-браузер AdsPower.
Профиль запускается по Local API, Playwright подключается к уже живому
браузеру через CDP, целевое действие выполняется с трёхуровневой обработкой
отказов (транспорт / страница / антибот).

**Блок 2** — оптимизация тяжёлой таблицы. Формулы убираются из листа,
расчёты уезжают в pandas, в Google Sheets возвращаются только готовые
значения одним пакетом. На 218 000 строк: витрина в 54 раза меньше сырья,
полный пересчёт за 0,85 с.

## Установка

```bash
poetry install
cp .env.example .env     # заполнить ключи AdsPower и Google
```

## Запуск

```bash
# Блок 1: собрать товары кабинета через профиль AdsPower
python scripts/run_ozon.py --profile <user_id> --url https://seller.ozon.ru/app/products

# Блок 2: сгенерировать синтетику и снять замеры
python scripts/generate_sample.py --rows 200000
python scripts/benchmark.py

# Блок 2: реальный прогон Google Sheets -> витрина -> Google Sheets
python scripts/sync_sheet.py
```

## Тесты

```bash
pytest          # 35 тестов, ~10 с
ruff check .
```

Тесты не требуют ни сети, ни браузера, ни сервисного аккаунта: Local API
замокан через `responses`, Sheets API — подставным объектом, Playwright
Page — двойником.

## Структура

```
src/ozon_test/
├── adspower/
│   ├── client.py     Local API: троттлинг, ретраи, разбор code != 0
│   ├── errors.py     иерархия ошибок по уровням реакции
│   ├── session.py    профиль -> CDP -> страница -> гарантированный stop
│   └── scenario.py   целевое действие, детект капчи, зависшая страница
└── sheets/
    ├── gsheets.py    пакетные чтение/запись, RAW, ретраи на 429
    ├── optimize.py   чистка -> дедупликация -> агрегация -> Parquet
    └── pipeline.py   Sheets -> pandas -> Parquet -> Sheets
```
