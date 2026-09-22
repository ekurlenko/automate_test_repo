# Тестовое задание: автоматизация Ozon и оптимизация таблиц

Ответ сопровождается работающим кодом — это не псевдокод из документа, а
проект с тестами. Всё, что ниже, можно запустить: команды указаны в конце
каждого блока, покрытие — 35 тестов, замеры сняты на сгенерированных 218 000
строках.

---

# Блок 1. Автоматизация кабинета Ozon через AdsPower Local API

## 1.1. Архитектура и логика работы

### Почему именно Local API, а не обычный Playwright

Ключевая вещь, вокруг которой строится весь блок: **браузер запускает AdsPower,
а не Playwright**. Отпечаток (canvas, WebGL, шрифты, User-Agent), прокси и куки
кабинета живут внутри профиля AdsPower. Если поднять браузер через
`playwright.chromium.launch()`, получится чистый Chromium — без отпечатка, без
прокси, без авторизации, то есть ровно то, от чего антидетект и защищает.

Поэтому схема такая: AdsPower запускает Chromium, отдаёт адрес отладочного
порта, а Playwright **подключается** к уже живому процессу через
`connect_over_cdp()`.

### Поток выполнения

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. GET /api/v1/browser/start?user_id=<id>&open_tabs=0&ip_tab=0   │
│    → { code: 0, data: { ws: { puppeteer: "ws://127.0.0.1:…" },   │
│                          debug_port: "9222" } }                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ ws.puppeteer
┌──────────────────────────────▼──────────────────────────────────┐
│ 2. playwright.chromium.connect_over_cdp(ws)                      │
│    browser.contexts[0]  ← СУЩЕСТВУЮЩИЙ контекст, не new_context  │
│    context.pages[0]                                              │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│ 3. Проверка IP: page.request.get("https://api.ipify.org")        │
│    IP не тот / пустой → работу не начинаем                       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│ 4. Целевое действие: открыть список товаров, дождаться           │
│    ключевого селектора, собрать строки по data-атрибутам         │
└──────────────────────────────┬──────────────────────────────────┘
                               │ finally — всегда
┌──────────────────────────────▼──────────────────────────────────┐
│ 5. GET /api/v1/browser/stop?user_id=<id>                         │
└─────────────────────────────────────────────────────────────────┘
```

### Четыре решения, которые определяют, будет ли это работать

**Контекст берём существующий.** `browser.contexts[0]`, а не `new_context()`.
Новый контекст создаёт чистый профиль внутри запущенного браузера и обнуляет
авторизацию в кабинете — сценарий упрётся в форму входа.

**Профиль всегда останавливается.** Запуск и остановка обёрнуты в
контекст-менеджер с `finally`. Без этого брошенные профили копятся: AdsPower
держит каждый как отдельный процесс Chromium, и через десяток прогонов машина
встаёт. Сам `stop()` не бросает исключений — иначе он затёр бы настоящую ошибку
сценария, из-за которой мы в `finally` и попали.

**Троттлинг встроен в клиент.** Local API ограничен одним запросом в секунду;
при превышении приходит `code=-1, Too many request` на случайном вызове.
Ограничение реализовано внутри `AdsPowerClient`, а не в вызывающем коде: про
него легко забыть, а диагностируется оно тяжело.

**HTTP 200 ничего не значит.** Local API отдаёт логические ошибки с кодом 200 и
полем `code != 0` в теле. Проверка идёт по `code`, а не по HTTP-статусу — это
самая частая ошибка при работе с этим API.

### Прокси

Мобильный прокси **не подключается из кода** — он настроен в самом профиле
AdsPower. Задача скрипта здесь другая:

* **проверить**, что трафик действительно идёт через прокси, до начала работы.
  Если прокси в профиле отвалился, AdsPower молча выпустит запросы с домашнего
  IP — и кабинет уедет в бан;
* **сменить IP** по rotate-ссылке провайдера, когда это нужно. У мобильных
  прокси ротация — это обычный HTTP-запрос к ссылке из личного кабинета.
  Ограничение операторов: обычно не чаще раза в 2 минуты.

## 1.2. Обработка ошибок

Ошибки разнесены по трём уровням, потому что на каждом нужна **своя** реакция.
Единый `try/except` с ретраем — главная причина, по которой такие скрипты
получают бан вместо данных.

| Уровень | Что случилось | Реакция | Где в коде |
|---|---|---|---|
| **Транспорт** | Local API не отвечает, таймаут, обрыв | Ретрай 3× с экспоненциальной паузой; троттлинг ≥1 с | `adspower/client.py` |
| **Транспорт** | `code != 0` (нет профиля, нет лицензии) | **Без ретрая** — ответ не изменится, нужно человеку | `ApiRejected` |
| **Профиль** | Запустился, но `ws.puppeteer` пустой | `ProfileStartError` — подключаться некуда, это провал, а не успех | `client.start()` |
| **Профиль** | Прокси не поднялся, IP не определяется | Стоп. Работать с домашнего IP нельзя | `scripts/run_ozon.py` |
| **Страница** | Селектор не появился за таймаут | `reload()` + повтор, до 3 попыток с растущей паузой | `scenario.open_page()` |
| **Страница** | Не помогло | `PageStalled` + скриншот в `artifacts/` | `scenario.open_page()` |
| **Антибот** | Капча, «Доступ ограничен», `/challenge` | **Не ретраим.** Скриншот → стоп профиля → ротация IP → пауза 3 мин → одна повторная попытка | `CaptchaDetected` |

Отдельные решения, которые стоит проговорить:

**Капча не ретраится в лоб.** Повторные попытки с того же IP только укрепляют
сигнал антифрода: вместо временной капчи получается устойчивый блок профиля.
Правильная последовательность — зафиксировать (скриншот для разбора),
отпустить профиль, сменить IP, подождать, вернуться один раз.

**Капчу мы не решаем.** Ни вручную, ни сервисами распознавания. Практически это
гонка, которую не выиграть, а с точки зрения правил площадки — прямое
нарушение. Если сценарий стабильно упирается в капчу, проблема не в скрипте:
слишком высокий темп, засвеченный прокси или задача, для которой есть
официальный Seller API.

**Проверка на антибот идёт до ожидания селектора.** Иначе скрипт потратит
полный 30-секундный таймаут, глядя на страницу капчи, и только потом сообщит
«элемент не найден» — диагностика получается ложной.

**`domcontentloaded` вместо `networkidle`.** Кабинет Ozon держит открытыми
вебсокеты и поллинг, сеть у него не затихает никогда — `networkidle` почти
гарантированно уходит в таймаут. Готовность страницы определяется по ключевому
селектору.

**Паузы с джиттером.** Ровные интервалы между действиями сами по себе выглядят
как автоматизация. Плюс это просто вежливо по отношению к чужому серверу.

**Данные читаются по `data`-атрибутам, а не по позициям колонок.** Вёрстка
кабинета меняется часто; `data-testid` переживает редизайн, а `nth-child(3)` —
нет. Отсутствующая ячейка не роняет сбор строки.

## 1.3. Код: запуск профиля и получение точки отладки

Фрагмент, который просили в задании, — целиком из `src/ozon_test/adspower/client.py`
и `session.py`:

```python
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class AdsPowerClient:
    def _throttle(self) -> None:
        """Local API: не чаще одного запроса в секунду."""
        with self._lock:
            elapsed = self._clock() - self._last_request_at
            if elapsed < self.min_interval:
                self._sleep(self.min_interval - elapsed)
            self._last_request_at = self._clock()

    @retry(
        # Ретраим только транспорт. Логические отказы API повторять бессмысленно.
        retry=retry_if_exception_type(LocalApiUnavailable),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _request(self, path: str, params: dict | None = None) -> dict:
        self._throttle()
        query = dict(params or {})
        if self.api_key:
            query["api_key"] = self.api_key

        try:
            response = self._session.get(f"{self.base_url}{path}", params=query, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise LocalApiUnavailable(
                f"Local API недоступен ({self.base_url}): {exc}. "
                "Проверьте, что приложение AdsPower запущено и Local API включён."
            ) from exc

        # Главная ловушка Local API: ошибки приезжают с HTTP 200.
        if payload.get("code") != 0:
            raise ApiRejected(payload.get("code", -1), payload.get("msg", "неизвестная ошибка"))
        return payload.get("data") or {}

    def start(self, user_id: str, *, headless: bool = False) -> BrowserEndpoint:
        data = self._request(
            "/api/v1/browser/start",
            {"user_id": user_id, "headless": int(headless), "open_tabs": 0, "ip_tab": 0},
        )
        ws = (data.get("ws") or {}).get("puppeteer", "")
        if not ws:
            raise ProfileStartError(f"профиль {user_id} запущен, но точки отладки нет: {data}")
        return BrowserEndpoint(
            user_id=user_id, ws_puppeteer=ws, debug_port=str(data.get("debug_port", "")),
            selenium=(data.get("ws") or {}).get("selenium", ""), webdriver=data.get("webdriver", ""),
        )

    def stop(self, user_id: str) -> None:
        """Не бросает: вызывается из finally и не должен затирать исходную ошибку."""
        try:
            self._request("/api/v1/browser/stop", {"user_id": user_id})
        except Exception as exc:
            log.warning("не удалось остановить профиль %s: %s", user_id, exc)
```

Подключение Playwright к запущенному браузеру:

```python
@contextmanager
def browser_page(client: AdsPowerClient, user_id: str, *, default_timeout_ms: int = 30_000):
    from playwright.sync_api import sync_playwright

    with profile(client, user_id) as endpoint:          # start + гарантированный stop
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint.cdp_url)
            # Контекст профиля уже существует — забираем его, а не создаём новый.
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            context.set_default_timeout(default_timeout_ms)
            page = context.pages[0] if context.pages else context.new_page()
            try:
                yield page, context
            finally:
                browser.close()   # рвём только соединение; браузер гасит AdsPower
```

Открытие страницы с полной обработкой отказов:

```python
def open_page(page, url, ready_selector, *, policy=None, timeout_ms=30_000, artifacts_dir="artifacts"):
    policy = policy or RetryPolicy()

    for attempt in range(1, policy.attempts + 1):
        try:
            if attempt == 1:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            else:
                page.reload(wait_until="domcontentloaded", timeout=timeout_ms)

            # Проверка антибота ДО ожидания селектора: иначе потратим весь таймаут
            # на страницу капчи и получим ложный диагноз.
            guard_antibot(page, artifacts_dir=artifacts_dir)
            page.wait_for_selector(ready_selector, timeout=timeout_ms)
            return
        except CaptchaDetected:
            raise                                   # наверх без ретраев
        except Exception as exc:
            if attempt < policy.attempts:
                policy.sleep(policy.delay_for(attempt))   # экспонента + джиттер

    capture_artifact(page, "stalled", artifacts_dir)
    raise PageStalled(f"{url}: селектор {ready_selector!r} не появился")
```

**Запуск:**

```bash
python scripts/run_ozon.py --profile <user_id> --url https://seller.ozon.ru/app/products
```

---

# Блок 2. Тяжёлая таблица: диагноз и лечение

## 2.1. Архитектура решения

### Диагноз

Таблица на десятки тысяч строк тормозит не из-за объёма как такового. Тормозит
она по трём конкретным причинам:

1. **Формулы по всему столбцу.** `VLOOKUP`/`ARRAYFORMULA` на `A:A` — это
   квадратичная зависимость: 50 000 строк × 50 000 строк поиска. Любая правка
   одной ячейки запускает пересчёт всего листа.
2. **Таблица используется как база данных.** В ней лежит и сырьё, и расчёты, и
   витрина. Sheets — не СУБД: у него потолок 10 млн ячеек, и задолго до него он
   перестаёт открываться.
3. **История версий.** Google хранит каждую правку. Скрипт, который пишет
   построчно, генерирует тысячи ревизий — файл распухает и медленно грузится.

### Принцип решения

> **Таблица — это витрина, а не база данных.**

Разделение обязанностей:

| Слой | Где живёт | Что делает |
|---|---|---|
| Сырьё | Parquet-файл (или БД) | Хранит всю историю, сжат, типизирован |
| Расчёты | Python + pandas | Чистка, дедупликация, агрегация |
| Витрина | Google Sheets | Только готовые **значения**. Ни одной формулы |

Лист, в котором нет формул, нечего пересчитывать — он открывается мгновенно
независимо от объёма.

### Конкретные меры

**Убрать формулы из листа полностью.** Всё, что считал `VLOOKUP`, считает
`pandas.merge`. Запись идёт с `valueInputOption="RAW"`: Sheets принимает
значения как есть и даже не пытается разбирать их как формулы.

**Читать и писать пакетами.** Один `values.get` на весь диапазон вместо тысячи
обращений по ячейке; один `values.update` на всю витрину вместо построчного
`append`. Квота Sheets API — 60 запросов в минуту на пользователя; построчная
запись упирается в неё на первой же сотне строк. Крупные выгрузки режутся на
куски по ~100 000 ячеек: формальный лимит выше, но большие запросы чаще ловят
таймаут шлюза, чем проходят.

**Задавать типы явно.** `article` как `category` вместо `object`, `qty` как
`int32` вместо `int64`. На синтетике это дало **12-кратную** экономию памяти
(56,6 МБ → 4,7 МБ).

**Хранить сырьё в Parquet, а не в CSV/Sheets.** Колоночный формат со сжатием
zstd, схема лежит внутри файла — при чтении не нужно заново угадывать типы.

**Чистить историю.** Раз в период — пересоздавать лист витрины (или копировать
таблицу в новую) вместо накопления ревизий.

**Что здесь важнее конкретных приёмов:** если витрину нужно обновлять регулярно
и с ней работают люди, это прямой кандидат на переезд из Sheets в BI-инструмент
или в базу с дашбордом. Оптимизация таблицы отодвигает потолок, но не убирает
его.

### Почему не Apps Script

Apps Script — рабочий вариант для простых случаев и триггеров «по изменению»,
но: лимит выполнения 6 минут, однопоточность, нет нормальных библиотек для
обработки данных, отладка болезненная. Как только логика сложнее «скопировать
диапазон», Python на своей стороне выигрывает по всем пунктам. Apps Script
уместен как кнопка в интерфейсе таблицы, которая дёргает вебхук на бэкенде.

## 2.2. Код

Функция полного прохода — `src/ozon_test/sheets/optimize.py`:

```python
RAW_DTYPES = {
    "date": "datetime64[ns]",
    "article": "category",     # кратная экономия памяти против object
    "warehouse": "category",
    "qty": "int32",
    "price": "float32",
    "revenue": "float32",
}

# Одна продажа = артикул + склад + день. Повтор по этому ключу — правка старой
# строки, а не вторая продажа.
KEY_COLUMNS = ("date", "article", "warehouse")


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Привести к схеме и выкинуть строки без ключа."""
    out = df.loc[:, list(RAW_DTYPES)].copy()

    out["date"] = pd.to_datetime(out["date"], errors="coerce", format="mixed")
    for column in ("article", "warehouse"):
        out[column] = out[column].astype("string").str.strip().replace("", pd.NA)
    for column in ("qty", "price", "revenue"):
        out[column] = _to_number(out[column]).fillna(0)   # "1 234,50" -> 1234.5

    # Строка без даты или артикула неинтерпретируема: агрегировать её некуда.
    out = out.dropna(subset=["date", "article"]).reset_index(drop=True)

    out["qty"] = out["qty"].astype("int32")
    out["price"] = out["price"].astype("float32")
    out["revenue"] = out["revenue"].astype("float32")
    for column in ("article", "warehouse"):
        out[column] = out[column].fillna("—").astype("category")
    return out


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """keep='last' — не вкусовщина: выгрузки дописываются в конец,
    нижняя строка по тому же ключу всегда свежее верхней."""
    return df.drop_duplicates(subset=list(KEY_COLUMNS), keep="last").reset_index(drop=True)


def aggregate_by_article(df: pd.DataFrame) -> pd.DataFrame:
    """Свернуть продажи по артикулу — это и есть витрина."""
    if df.empty:
        return pd.DataFrame(columns=list(MART_COLUMNS))

    mart = (
        # observed=True обязателен: без него category разворачивается в декартово
        # произведение всех категорий и витрина распухает пустыми строками.
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


def save_compact(df: pd.DataFrame, dest) -> Path:
    """Parquet + zstd: компактно, и схема лежит внутри файла."""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
    return path


def optimize_sales(source, dest) -> tuple[pd.DataFrame, OptimizeReport]:
    """Полный проход: сырой файл -> чистка -> дедупликация -> витрина -> Parquet."""
    raw = read_raw(source)
    normalized = normalize(raw)
    deduped = deduplicate(normalized)
    mart = aggregate_by_article(deduped)
    dest_path = save_compact(mart, dest)
    return mart, OptimizeReport(...)   # что именно сделали — в лог и в отчёт
```

Запись витрины обратно в таблицу — `src/ozon_test/sheets/gsheets.py`:

```python
def write_dataframe(self, range_name: str, df: pd.DataFrame, *, clear_first: bool = True) -> int:
    # clear_first обязателен, когда витрина может сократиться: иначе внизу
    # останется хвост прошлой выгрузки.
    if clear_first:
        self._clear(range_name)
    if df.empty:
        return 0

    payload = [list(df.columns)] + _to_cells(df)
    rows_per_chunk = max(1, MAX_CELLS_PER_REQUEST // max(1, len(df.columns)))

    for offset in range(0, len(payload), rows_per_chunk):
        self._update(f"{sheet}!A{start_row + offset}", payload[offset : offset + rows_per_chunk])
    return len(payload) - 1


@_with_retry          # ретрай только на 429/5xx, экспоненциальная пауза
def _update(self, range_name: str, chunk: list[list]) -> None:
    self.values.update(
        spreadsheetId=self.spreadsheet_id,
        range=range_name,
        # RAW: пишем значения как есть. USER_ENTERED заставил бы Sheets
        # разбирать каждую ячейку и превращать строки вида "=…" в формулы.
        valueInputOption="RAW",
        body={"values": chunk},
    ).execute()
```

## 2.3. Замеры

Синтетика, приближенная к реальной выгрузке: 218 000 строк, 4 000 артикулов,
~8% дублей, ~1% битых строк, цены в человеческом формате `1 234,50`.

```
=== память на сыром кадре ===
наивно (object):      56.6 МБ  за 0.18 с
с типами + дедуп:      4.7 МБ  за 0.84 с
экономия памяти:      в 12.1x

=== полный пайплайн ===
строк на входе:  218 000
отброшено битых:   2 000
снято дублей:     19 677
строк в витрине:   4 000
объём: 12.49 МБ -> 0.09 МБ (в 145.8x компактнее)
время: 0.85 с

=== витрина на диске ===
CSV:     0.3 МБ
Parquet: 0.1 МБ (в 3.2x компактнее)
```

Главная цифра — не память, а **218 000 строк → 4 000**. В таблицу уезжает
витрина в 54 раза меньше сырья, и в ней нет ни одной формулы. Полный пересчёт
занимает 0,85 секунды против минут ожидания в самой таблице.

**Запуск:**

```bash
python scripts/generate_sample.py --rows 200000   # сгенерировать сырьё
python scripts/benchmark.py                        # замеры выше
python scripts/sync_sheet.py                       # реальный прогон Sheets -> Sheets
```

---

# Что в проекте

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
scripts/              точки входа + генератор синтетики + бенчмарк
tests/                35 тестов, без сети и без браузера
```

**Тесты:** `pytest` — 35 штук, проходят за ~10 с. Local API замокан через
`responses`, Sheets API — подставным объектом, Playwright Page — двойником.
Ни сеть, ни браузер, ни сервисный аккаунт не нужны.

Дефект, который тест поймал по ходу работы: самый первый запрос к Local API
честно отстаивал полную секунду троттлинга, хотя перед ним ничего не было.

Ещё два места тесты не столько нашли, сколько закрепили — это решения, в
которых легко откатиться назад при следующей правке:

* `groupby` по `category` без `observed=True` развернул бы витрину в декартово
  произведение категорий;
* средняя цена реализации — это выручка, делённая на штуки; среднее по колонке
  `price` не взвешено по количеству и даёт неверный ответ.
