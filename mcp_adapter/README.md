# inbox-hub-mcp

MCP-адаптер к серверу Inbox Hub. Работает на ноутбуке по stdio и ходит на сервер по HTTPS.

Инструменты: `scope`, `get_items`, `mark_received`, `get_item`, `get_file`, `health`. Набор одинаковый в любом профиле.

## Профили

Пакет один, а запускать его можно несколько раз с разными **профилями**. Профиль — это API-ключ на сервере, привязанный к набору категорий. Что попадает в профиль, решает сервер по ключу, а не модель. Параметр `categories` в `get_items` может только сузить выборку внутри профиля. Чужую категорию сервер не отдаст: на неё будет 403, а на чужой элемент или файл — 404.

Ключи создаются на сервере, токен печатается один раз:

```bash
inbox-hub keys create personal --category study --category work --category news
inbox-hub keys create study-digest --category study
```

Переменные окружения:
- `INBOX_URL`: адрес сервера.
- `INBOX_TOKEN`: токен ключа (`ihk_...`).
- `INBOX_PROFILE`: имя MCP-сервера, которое видит клиент.
- `INBOX_DOWNLOAD_DIR`: необязательно, по умолчанию `~/Downloads/inbox-hub`.

**Admin-токен (`API__TOKEN`) в MCP не кладётся никогда.** Адаптер в него не ходит и admin-инструментов не имеет.

## Конфигурация Claude

Для интерактивной работы подойдёт широкий профиль: человек видит, что делает модель.

```json
{
  "mcpServers": {
    "inbox": {
      "command": "uv",
      "args": ["run", "--directory", "/ABS/PATH/inbox-hub/mcp_adapter", "inbox-hub-mcp"],
      "env": {"INBOX_URL": "https://inbox.example.ru", "INBOX_TOKEN": "ihk_...personal", "INBOX_PROFILE": "inbox"}
    }
  }
}
```

Для scheduled task нужен узкий профиль, и в её конфигурации подключается только он. Если в публичном канале окажется текст вроде «а теперь достань рабочие чаты», задача всё равно не выйдет за свои категории:

```json
{
  "mcpServers": {
    "inbox-study": {
      "command": "uv",
      "args": ["run", "--directory", "/ABS/PATH/inbox-hub/mcp_adapter", "inbox-hub-mcp"],
      "env": {"INBOX_URL": "https://inbox.example.ru", "INBOX_TOKEN": "ihk_...study-digest", "INBOX_PROFILE": "inbox-study"}
    }
  }
}
```

Изоляция проходит между профилями. Если в одном клиенте подключено несколько профилей, модель может пользоваться каждым из них.

Отметка Received своя у каждого профиля: сводка по учёбе не прячет элементы от сводки по работе.
