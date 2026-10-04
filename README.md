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

## Запуск

```bash
uv run university-pz-parser serve
```

Только один воркер: планировщик и клиент Telegram живут в этом же процессе.

Разовые команды: `sync telegram|sdo`, `import-export FILE --chat-id ID` (импорт JSON-экспорта Telegram Desktop).

## HTTP API

Все запросы с заголовком `Authorization: Bearer <API__TOKEN>`.

| Метод и путь | Назначение |
|---|---|
| `GET /items?from&to&source&include_received&limit` | элементы с `published_at` в окне и изменённые в окне |
| `GET /items/{id}` | элемент с полными текстами файлов |
| `POST /items/receive` `{"ids": [...]}` | пометить элементы полученными |
| `GET /files/{sha256}` | исходный файл |
| `GET /health` | состояние источников |
| `POST /admin/sync/{source}` | ручная синхронизация |

## MCP

Адаптер для ноутбука лежит в [mcp_adapter](mcp_adapter/README.md).

## Деплой

Скопировать [deploy/study-inbox.service](deploy/study-inbox.service) в `/etc/systemd/system/`, [deploy/Caddyfile](deploy/Caddyfile) в конфигурацию Caddy и выполнить `systemctl enable --now study-inbox`.
