# Inbox Hub

Собирает сообщения Telegram-чатов и материалы СДО (`sdo.timacad.ru`) в одну базу, достаёт текст из файлов и раскладывает каналы по **категориям**: учёба, работа, анонсы, хакатоны, новости и так далее. ИИ получает ленту за промежуток времени через HTTP API и MCP, причём каждый API-ключ видит только свои категории. Термины описаны в [CONTEXT.md](CONTEXT.md), решения — в [спецификации](docs/superpowers/specs/2026-10-04-study-inbox-design.md).

## Настройка

```bash
uv sync
cp .env.example .env
uv run inbox-hub login
uv run inbox-hub list-chats
uv run inbox-hub init-db
```

В `.env` указываются `TELEGRAM__*`, `SDO__*` и `API__TOKEN` (admin). `SDO__BACKEND=auto` включает мобильный REST СДО, если он выдаёт токен. По умолчанию используется разбор HTML.

Оценки преподавателя (только СДО) приходят в поле `grade` тех же элементов. Итог за курс — отдельный элемент с `kind=course_grade`. В REST-режиме оценки есть только у заданий.

## Категории, чаты и ключи

```bash
inbox-hub categories create study --title "Учёба" --auto-source sdo   # новые курсы СДО попадают сюда сами
inbox-hub categories create work --title "Работа"
inbox-hub chats add -1001234567890 --category work                     # id из list-chats; начинает собираться
inbox-hub categories add-channel study telegram:-1009876543210         # канал может быть в нескольких категориях
inbox-hub channels list
inbox-hub keys create study-digest --category study                    # печатает токен один раз
inbox-hub keys grant study-digest news | ungrant | rotate | revoke
```

Команды работают с базой напрямую, поэтому их можно запускать рядом с работающим сервером. В Docker: `docker compose exec inbox-hub inbox-hub ...`. Новый чат подхватывается в течение минуты, а история за `TELEGRAM__BACKFILL_FROM` подтягивается на ближайшей синхронизации (или сразу через `POST /admin/sync/telegram`).

Пока сервер работает, `list-chats` из CLI не запустится, потому что сессия Telegram занята. Список диалогов в этом случае отдаёт `GET /admin/telegram/dialogs`.

Канал без категорий собирается, но виден только admin-токену.

### Обновление со Study Inbox

Миграция `0002` кладёт все существующие каналы в категорию `study` и переносит отметки Received на ключ `study`. Этот ключ создаётся неактивным. После `init-db` выполните `inbox-hub keys rotate study` и пропишите полученный токен в MCP.

## Запуск

```bash
uv run inbox-hub serve
```

Нужен ровно один воркер: планировщик и клиент Telegram живут в этом же процессе.

Разовые команды: `sync telegram|sdo` и `import-export FILE --chat-id ID` (импорт JSON-экспорта Telegram Desktop).

## Доступ

Все запросы идут с заголовком `Authorization: Bearer <token>`.

- **API-ключ** (`ihk_...`, создаётся `inbox-hub keys create`) видит только элементы и файлы из своих категорий. Чужой элемент или файл — 404, чужая категория в `categories` — 403. Отметка Received у каждого ключа своя.
- **Admin** (`API__TOKEN`) видит всё и управляет категориями через `/admin`. В MCP его не кладут.

| Метод и путь | Назначение |
|---|---|
| `GET /items?from&to&source&categories&include_received&limit` | элементы с `published_at` в окне и изменённые в окне |
| `GET /items/{id}` | элемент с полными текстами файлов |
| `POST /items/receive` `{"ids": [...]}` | пометить элементы полученными этим ключом |
| `GET /files/{sha256}` | исходный файл |
| `GET /me` | имя ключа и его категории |
| `GET /health` | состояние источников (подробности только admin) |
| `POST /admin/sync/{source}` | ручная синхронизация |
| `/admin/categories`, `/admin/channels`, `/admin/telegram/chats`, `/admin/telegram/dialogs`, `/admin/keys` | управление (только admin) |

## MCP

Адаптер для ноутбука лежит в [mcp_adapter](mcp_adapter/README.md). Это один пакет, который запускается с разными профилями (ключами).

## Деплой

Скопировать [deploy/inbox-hub.service](deploy/inbox-hub.service) в `/etc/systemd/system/`, [deploy/Caddyfile](deploy/Caddyfile) — в конфигурацию Caddy, затем выполнить `systemctl enable --now inbox-hub`.

В Docker: `docker compose up -d --build`. Том данных закреплён под старым именем `university-pz-parser_inbox-data`, поэтому данные сохраняются после переименования.
