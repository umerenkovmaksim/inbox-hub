# Inbox Hub

Собирает сообщения Telegram-чатов и материалы СДО (`sdo.timacad.ru`) в одну базу, достаёт текст из файлов и раскладывает каналы по **категориям**: учёба, работа, анонсы, хакатоны, новости и так далее. ИИ получает ленту за промежуток времени через HTTP API и MCP, причём каждый API-ключ видит только свои категории. Термины описаны в [CONTEXT.md](CONTEXT.md), решения — в [спецификации](docs/superpowers/specs/2026-10-04-study-inbox-design.md).

## Настройка

```bash
uv sync
cp .env.example .env
uv run inbox-hub init-db
uv run inbox-hub login        # один раз; сессия сохраняется в data/telegram.session
```

В `.env` указываются `TELEGRAM__*`, `SDO__*` и `API__TOKEN` (admin). `SDO__BACKEND=auto` включает мобильный REST СДО, если он выдаёт токен. По умолчанию используется разбор HTML.

Оценки преподавателя (только СДО) приходят в поле `grade` тех же элементов. Итог за курс — отдельный элемент с `kind=course_grade`. В REST-режиме оценки есть только у заданий.

## Категории, чаты и ключи

```bash
inbox-hub categories create study --title "Учёба" --auto-source sdo   # новые курсы СДО попадают сюда сами
inbox-hub categories create work --title "Работа"
inbox-hub list-chats                                                   # группы и каналы аккаунта
inbox-hub chats add -1001234567890 --category work                     # id из list-chats; начинает собираться
inbox-hub categories add-channel study telegram:-1009876543210         # канал может быть в нескольких категориях
inbox-hub channels list
inbox-hub keys create study-digest --category study                    # печатает токен один раз
inbox-hub keys grant study-digest news | ungrant | rotate | revoke
```

### Каталог в файле

То же самое можно описать в `catalog.yaml` и применять целиком. Пример с пояснениями лежит в [catalog.example.yaml](catalog.example.yaml).

```bash
inbox-hub catalog export -o catalog.yaml   # текущее состояние базы, каналы подписаны названиями
inbox-hub catalog plan                     # что изменится; база не трогается
inbox-hub catalog apply                    # показывает план, спрашивает подтверждение, применяет одной транзакцией
inbox-hub catalog apply --prune            # плюс удаляет то, чего нет в файле
```

- Без `--prune` apply только добавляет и обновляет, а удаления показывает отдельным списком. С `--prune` удаляются категории, вхождения каналов, доступы ключей к категориям и отслеживание чатов, которых нет в файле. Опечатка в slug превращается в удаление категории, поэтому сначала запускайте `plan --prune`.
- Telegram-чат, указанный в `channels` любой категории, собирается. `telegram.track` — чаты без категорий, `telegram.paused` — чаты, которые не собираются, но остаются в категориях.
- Каналы из `auto_sources` категории при `--prune` не удаляются. Чтобы убрать такой курс, внесите его в `exclude`.
- Курс СДО, которого ещё нет в базе, пропускается с предупреждением: он появится после синхронизации.
- Токенов в файле нет. Для нового ключа `apply` печатает токен один раз, `keys rotate` и `keys revoke` остаются командами. Ключи, которых нет в файле, не трогаются.
- `catalog.yaml` содержит id личных чатов и уже добавлен в `.gitignore`.

В Docker файл передаётся через stdin, поэтому подтверждение нужно дать флагом: `docker compose exec -T inbox-hub inbox-hub catalog plan -f - < catalog.yaml`, затем `... catalog apply -f - --yes < catalog.yaml`.

Команды работают с базой напрямую, поэтому их можно запускать рядом с работающими процессами. В Docker: `docker compose exec inbox-hub inbox-hub ...`. Новый чат коллектор подхватывает в течение минуты и сразу дочитывает историю с `TELEGRAM__BACKFILL_FROM`.

`list-chats` и `GET /admin/telegram/dialogs` показывают снимок групп и каналов с последней сверки коллектора (в Telegram не ходят). Пока коллектор ни разу не запускался, список пуст.

Канал без категорий собирается, но виден только admin-токену.

### Обновление со Study Inbox

Миграция `0002` кладёт все существующие каналы в категорию `study` и переносит отметки Received на ключ `study`. Этот ключ создаётся неактивным. После `init-db` выполните `inbox-hub keys rotate study` и пропишите полученный токен в MCP.

## Запуск

Два процесса на одной базе:

```bash
uv run inbox-hub collect   # единственный процесс, который ходит в Telegram
uv run inbox-hub serve     # HTTP API + СДО по расписанию; Telegram не трогает (один воркер)
```

`collect` держит lock-файл `data/telegram.lock`: второй процесс с той же сессией (ещё один `collect` или `login`) не запустится. Новые сообщения, правки и удаления приходят событиями (`catch_up=True` догоняет пропущенное за время простоя). При старте и раз в `TELEGRAM__RECONCILE_INTERVAL_HOURS` (по умолчанию 4) сверка через `iter_dialogs` дочитывает историю только тех отслеживаемых чатов, где Telegram видит сообщения новее сохранённых. Удалённые сообщения не стираются: они получают `deleted_at` и пропадают из `/items`. Исходный `message.to_dict()` хранится в `items.raw`. Скачиваются документы и фото (`TELEGRAM__DOWNLOAD_KINDS`), по одному.

Параметры клиента (`TELEGRAM__DEVICE_MODEL`, `TELEGRAM__SYSTEM_VERSION`, `TELEGRAM__APP_VERSION`) задайте один раз и не меняйте.

Разовые команды: `sync sdo` и `import-export FILE --chat-id ID` (импорт JSON-экспорта Telegram Desktop).

### Переход на отдельный коллектор

1. Остановить старый `serve`: раньше он сам подключался к Telegram, и два подключения с одной сессией недопустимы.
2. `inbox-hub init-db` — миграция `0003`: колонки `raw` и `deleted_at`, таблица снимка диалогов, курсоры чатов поднимаются до последнего сохранённого `msg_id`. Данные не трогаются.
3. Запустить `collect` и `serve`. Файл сессии используется как есть, повторный `login` не нужен.

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
| `POST /admin/sync/sdo` | ручная синхронизация СДО (Telegram из API не запускается) |
| `/admin/categories`, `/admin/channels`, `/admin/telegram/chats`, `/admin/telegram/dialogs`, `/admin/keys` | управление (только admin) |

## MCP

Адаптер для ноутбука лежит в [mcp_adapter](mcp_adapter/README.md). Это один пакет, который запускается с разными профилями (ключами).

## Деплой

Скопировать [deploy/inbox-hub.service](deploy/inbox-hub.service) и [deploy/inbox-hub-collector.service](deploy/inbox-hub-collector.service) в `/etc/systemd/system/`, [deploy/Caddyfile](deploy/Caddyfile) — в конфигурацию Caddy, затем выполнить `systemctl enable --now inbox-hub inbox-hub-collector`.

В Docker: `docker compose up -d --build`. Сервис `inbox-hub` сначала применяет миграции, затем запускает `serve`; `collector` стартует, только когда `inbox-hub` стал healthy. Том данных закреплён под старым именем `university-pz-parser_inbox-data`, поэтому данные сохраняются после переименования.
