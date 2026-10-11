# Study Inbox

Собирает сообщения учебных Telegram-чатов и материалы СДО (`sdo.timacad.ru`) в одну базу, достаёт текст из файлов и отдаёт ленту за промежуток времени через HTTP API и MCP. Термины описаны в [CONTEXT.md](CONTEXT.md), решения — в [спецификации](docs/superpowers/specs/2026-10-04-study-inbox-design.md).

## Настройка

```bash
uv sync
cp .env.example .env
uv run university-pz-parser login
uv run university-pz-parser list-chats
uv run university-pz-parser init-db
```

В `.env` указываются `TELEGRAM__*`, `SDO__*`, `API__TOKEN`. Идентификаторы чатов из `list-chats` записываются в `TELEGRAM__SOURCE_IDS`. `SDO__BACKEND=auto` включает мобильный REST СДО, если он выдаёт токен; по умолчанию используется разбор HTML.

Оценки преподавателя (только СДО) приходят в поле `grade` тех же элементов; итог за курс — элемент с `kind=course_grade`. В REST-режиме оценки есть только у заданий.

## Запуск

Два процесса на одной базе:

```bash
uv run university-pz-parser collect   # единственный процесс, который ходит в Telegram
uv run university-pz-parser serve     # HTTP API + СДО по расписанию; Telegram не трогает
```

`collect` держит lock-файл `data/telegram.lock`: второй процесс с той же сессией (ещё один `collect`, `login`, `list-chats`) не запустится. Новые сообщения, правки и удаления приходят событиями (`catch_up=True` догоняет пропущенное за время простоя); раз в `TELEGRAM__RECONCILE_INTERVAL_HOURS` (по умолчанию 4) сверка через `iter_dialogs` дочитывает историю только тех чатов, где Telegram видит сообщения новее сохранённых. Удалённые сообщения не стираются, а получают `deleted_at` и пропадают из `/items`. Исходный `message.to_dict()` хранится в `items.raw`.

`serve` держит только один воркер (планировщик СДО живёт в процессе).

Разовые команды: `sync sdo`, `import-export FILE --chat-id ID` (импорт JSON-экспорта Telegram Desktop). `list-chats` требует остановленного `collect`.

### Первый запуск после перехода на `collect`

1. Остановить старый `serve` (раньше он сам подключался к Telegram).
2. `uv run university-pz-parser init-db` — миграция `0002`: колонки `raw`, `deleted_at`, курсоры чатов поднимаются до последнего сохранённого `msg_id`. Данные не трогаются.
3. Запустить `collect` и `serve` (или `docker compose up -d`, systemd-юниты ниже). Файл сессии `data/telegram.session` используется как есть, повторный `login` не нужен.
4. Параметры клиента (`TELEGRAM__DEVICE_MODEL`, `TELEGRAM__SYSTEM_VERSION`, `TELEGRAM__APP_VERSION`) задать один раз и больше не менять.

## HTTP API

Все запросы с заголовком `Authorization: Bearer <API__TOKEN>`.

| Метод и путь | Назначение |
|---|---|
| `GET /items?from&to&source&include_received&limit` | элементы с `published_at` в окне и изменённые в окне |
| `GET /items/{id}` | элемент с полными текстами файлов |
| `POST /items/receive` `{"ids": [...]}` | пометить элементы полученными |
| `GET /files/{sha256}` | исходный файл |
| `GET /health` | состояние источников |
| `POST /admin/sync/sdo` | ручная синхронизация СДО (Telegram только через `collect`) |

## MCP

Адаптер для ноутбука лежит в [mcp_adapter](mcp_adapter/README.md).

## Деплой

`docker compose up -d` поднимает `study-inbox` (сначала применяет миграции, затем `serve`) и `collector`, который стартует только после того, как `study-inbox` стал healthy. Оба на общем томе `inbox-data`.

Без Docker: скопировать [deploy/study-inbox.service](deploy/study-inbox.service) и [deploy/study-inbox-collector.service](deploy/study-inbox-collector.service) в `/etc/systemd/system/`, [deploy/Caddyfile](deploy/Caddyfile) в конфигурацию Caddy и выполнить `systemctl enable --now study-inbox study-inbox-collector`.
