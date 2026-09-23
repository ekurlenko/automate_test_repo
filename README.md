# Автоматизация кабинета Ozon и оптимизация таблиц

Тестовое задание. Развёрнутый ответ — в
[документе](https://docs.google.com/document/d/1H7Cq7SsW5YFqH9nfLKjqwbg63dicofaHI2qY_7Q7dHk/edit),
здесь работающий код.

**Блок 1** — клиент AdsPower Local API: открывает профиль по ID и отдаёт вебдрайвер с
портом отладки, к которому подключается Playwright или Selenium. Троттлинг, ретраи
транспорта, разбор `code != 0` при HTTP 200, настройка прокси в профиле и ротация IP.
Проверено на AdsPower 8.7.23.

**Блок 2** — витрина продаж: чистка, дедупликация, агрегация по артикулам. Формулы из
листа убираются, расчёты уезжают в pandas, в Google Sheets возвращаются готовые
значения одним пакетом. На 218 000 строк витрина в 55 раз компактнее сырья и собирается
за секунду. Проверено на живой таблице.

## Установка

```bash
poetry install
cp .env.example .env     # заполнить ключи AdsPower и Google
```

## Проверка

```bash
pytest              # 32 теста, ~10 с, без сети и браузера
ruff check .
```

Тестам не нужны ни AdsPower, ни сервисный аккаунт: Local API замокан через `responses`,
Sheets API — подставным объектом.

## Запуск

```bash
python -m adspower --profile <user_id>    # вебдрайвер и порт отладки
python -m sheets sample --rows 200000     # сгенерировать сырые данные
python -m sheets bench                    # замеры
python -m sheets sync                     # Google Sheets -> витрина -> Sheets
python -m sheets sync --csv data/mart.csv # то же плюс снимок витрины файлом
```

## Структура

```
src/
├── config/           общее
│   ├── env.py        .env и секреты: единственное место, где читается окружение
│   └── logs.py       настройка логирования для точек входа
├── adspower/         блок 1, пункт 3
│   ├── settings.py   адрес Local API, лимит частоты, таймауты
│   ├── errors.py     ApiDown / ApiRejected / NoDebugPort
│   ├── models.py     Endpoint: webdriver, debug_port, cdp, selenium
│   ├── client.py     Local API: профиль, точка отладки, прокси, ротация IP
│   └── main.py       python -m adspower
└── sheets/           блок 2
    ├── settings.py   схема данных, пути, лимиты Sheets API
    ├── models.py     Report, Synced
    ├── mart.py       чистка -> дедупликация -> агрегация -> CSV
    ├── client.py     пакетные чтение и запись, RAW, ретраи на 429
    └── main.py       python -m sheets sample | bench | sync
```
