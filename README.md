## Установка

```bash
poetry install
cp .env.example .env     # заполнить ключи AdsPower и Google
```

## Запуск

```bash
python -m adspower --profile <user_id>    # вебдрайвер и порт отладки
python -m sheets sample --rows 200000     # сгенерировать сырые данные
python -m sheets bench                    # замеры
python -m sheets sync                     # Google Sheets -> витрина -> Sheets
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
