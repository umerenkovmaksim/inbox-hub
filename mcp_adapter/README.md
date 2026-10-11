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

Адаптер запускается прямо из GitHub через `uvx` и обновляется сам: `--refresh-package` при каждом старте MCP-сервера проверяет свежую версию из `main`, а зависимости (`mcp`, `httpx`) берутся из кэша. `main` — та же ветка, что задеплоена, поэтому адаптер и сервер не расходятся по версиям. Новая версия подхватывается при следующем запуске MCP-сервера: новая сессия Claude Code, перезапуск Claude Desktop, каждый запуск scheduled task. Уже работающий процесс сам не перезапускается.

Репозиторий приватный, uv скачивает его через системный git. Нужен SSH-ключ, добавленный в GitHub (как в примерах ниже), или `git+https://github.com/...` при настроенном доступе по HTTPS (`gh auth setup-git`). При каждом старте нужен доступ к GitHub.

Для интерактивной работы подойдёт широкий профиль: человек видит, что делает модель.

```json
{
  "mcpServers": {
    "inbox": {
      "command": "uvx",
      "args": [
        "--refresh-package", "inbox-hub-mcp",
        "--from", "git+ssh://git@github.com/umerenkovmaksim/university-pz-parser@main#subdirectory=mcp_adapter",
        "inbox-hub-mcp"
      ],
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
      "command": "uvx",
      "args": [
        "--refresh-package", "inbox-hub-mcp",
        "--from", "git+ssh://git@github.com/umerenkovmaksim/university-pz-parser@main#subdirectory=mcp_adapter",
        "inbox-hub-mcp"
      ],
      "env": {"INBOX_URL": "https://inbox.example.ru", "INBOX_TOKEN": "ihk_...study-digest", "INBOX_PROFILE": "inbox-study"}
    }
  }
}
```

### Запуск из локального клона

Без доступа к GitHub при старте можно запускать из клона: `"command": "uv", "args": ["run", "--directory", "/ABS/PATH/university-pz-parser/mcp_adapter", "inbox-hub-mcp"]`. Тогда адаптер сам не обновляется: после изменений нужен `git pull` в клоне и перезапуск клиента.

Изоляция проходит между профилями. Если в одном клиенте подключено несколько профилей, модель может пользоваться каждым из них.

Отметка Received своя у каждого профиля: сводка по учёбе не прячет элементы от сводки по работе.

## Переход со Study Inbox

Старая конфигурация (`study-inbox-mcp` и admin-токен) после обновления сервера не работает: адаптер переименован, а отметки Received теперь у каждого ключа свои, и admin-токен их не ставит.

1. На сервере выпустить токен для ключа `study`, который миграция создала неактивным и на который перенесла старые отметки: `inbox-hub keys rotate study`. В Coolify — терминал контейнера `inbox-hub`, в Docker — `docker compose exec inbox-hub inbox-hub keys rotate study`.
2. В конфигурации MCP заменить запуск на `inbox-hub-mcp` (см. выше), `INBOX_TOKEN` — на полученный `ihk_...`, добавить `INBOX_PROFILE`.
3. Перезапустить клиент.
