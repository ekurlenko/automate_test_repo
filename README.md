# Ozon + AdsPower: тестовое задание

Ответ на тестовое задание целиком: **[docs/ANSWER.md](docs/ANSWER.md)**.

Репозиторий — не иллюстрация к документу, а работающий код с тестами.

## Что здесь

**Блок 1** — автоматизация кабинета Ozon через антидетект-браузер AdsPower.
Задание просит описать архитектуру (п. 1), план обработки ошибок (п. 2) и
реализовать кодом п. 3 — запрос к Local API на открытие профиля и получение
вебдрайвера с портом отладки. Код — ровно про это.

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
python -m adspower --profile <user_id>    # вебдрайвер и порт отладки
python -m sheets sample --rows 200000     # сгенерировать сырьё
python -m sheets bench                    # замеры
python -m sheets sync                     # Google Sheets -> витрина -> Sheets
```

Витрина ложится в `data/mart.parquet` (рабочий формат) и `data/mart.csv`
(готов к импорту в Google Sheets руками).

## Тесты

```bash
pytest          # 35 тестов, ~10 с
ruff check .
```

Тесты не требуют ни сети, ни браузера, ни сервисного аккаунта: Local API
замокан через `responses`, Sheets API — подставным объектом.

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
│   ├── client.py     запрос к Local API, открытие и закрытие профиля
│   └── main.py       python -m adspower
└── sheets/           блок 2
    ├── settings.py   схема данных, пути, лимиты Sheets API
    ├── models.py     Report, Synced
    ├── mart.py       чистка -> дедупликация -> агрегация -> Parquet/CSV
    ├── client.py     пакетные чтение и запись, RAW, ретраи на 429
    └── main.py       python -m sheets sample | bench | sync
```

Секретов в коде нет: ключи читаются из `.env` (он в `.gitignore`), шаблон —
`.env.example`. Экспортировать переменные руками не нужно, `config/env.py`
подхватывает файл сам.
