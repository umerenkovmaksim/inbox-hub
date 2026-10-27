# inbox-hub-mcp

[English](README.md) · [Русский](README.ru.md)

MCP-адаптер к серверу Inbox Hub. Работает на ноутбуке по stdio и ходит на сервер по HTTPS.

Инструменты: `scope`, `get_items`, `mark_received`, `get_item`, `get_file`, `health`. Набор одинаковый в любом профиле.

## Профили

Пакет один, а запускать его можно несколько раз с разными **профилями**. Профиль — это API-ключ на сервере, привязанный к набору категорий. Что попадает в профиль, решает сервер по ключу, а не модель. Параметр `categories` в `get_items` может только сузить выборку внутри профиля. Чужую категорию сервер не отдаст: на неё будет 403, а на чужой элемент или файл — 404.

Ключи создаются на сервере из корня репозитория, токен печатается один раз. Категории должны быть [созданы заранее](../docs/catalog.md):

```bash
uv run inbox-hub keys create personal --category study --category work --category news
uv run inbox-hub keys create study-digest --category study
```

Переменные окружения:

- `INBOX_URL`: адрес сервера.
- `INBOX_TOKEN`: токен ключа (`ihk_...`).
- `INBOX_PROFILE`: имя MCP-сервера, которое видит клиент.
- `INBOX_DOWNLOAD_DIR`: необязательно, по умолчанию `~/Downloads/inbox-hub`.

**Admin-токен (`API__TOKEN`) в MCP не кладётся никогда.** Адаптер в него не ходит и admin-инструментов не имеет.

## Конфигурация MCP-клиента

Адаптер запускается прямо из GitHub через `uvx` и обновляется сам: `--refresh-package` при каждом старте MCP-сервера проверяет свежую версию из `main`, а зависимости (`mcp`, `httpx`) берутся из кэша. Если сервер развёрнут из другой ревизии, закрепите в URL тот же commit SHA вместо `main`. Новая версия подхватывается при следующем запуске MCP-сервера: новая сессия Claude Code, перезапуск Claude Desktop, каждый запуск scheduled task. Уже работающий процесс сам не перезапускается.

Репозиторий публичный: установка по HTTPS не требует GitHub-аккаунта или SSH-ключа. На компьютере клиента должны быть установлены `uv`, Git и Python 3.12+ (серверу нужен Python 3.14+). При каждом старте с `--refresh-package` нужен доступ к GitHub. Формат ниже подходит для клиентов с конфигурацией `mcpServers`; расположение файла зависит от клиента.

Для интерактивной работы подойдёт широкий профиль: человек видит, что делает модель.

```json
{
  "mcpServers": {
    "inbox": {
      "command": "uvx",
      "args": [
        "--refresh-package", "inbox-hub-mcp",
        "--from", "git+https://github.com/umerenkovmaksim/inbox-hub@main#subdirectory=mcp_adapter",
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
        "--from", "git+https://github.com/umerenkovmaksim/inbox-hub@main#subdirectory=mcp_adapter",
        "inbox-hub-mcp"
      ],
      "env": {"INBOX_URL": "https://inbox.example.ru", "INBOX_TOKEN": "ihk_...study-digest", "INBOX_PROFILE": "inbox-study"}
    }
  }
}
```

### Запуск из локального клона

Для запуска без обращения к GitHub при старте используйте локальный клон. Один раз установите зависимости: `uv sync --locked --directory /ABS/PATH/inbox-hub/mcp_adapter`, затем настройте клиент:

```json
{
  "mcpServers": {
    "inbox": {
      "command": "uv",
      "args": ["run", "--directory", "/ABS/PATH/inbox-hub/mcp_adapter", "inbox-hub-mcp"],
      "env": {
        "INBOX_URL": "https://inbox.example.ru",
        "INBOX_TOKEN": "ihk_...personal",
        "INBOX_PROFILE": "inbox"
      }
    }
  }
}
```

Замените абсолютный путь и учётные данные. Адаптер не обновляется автоматически: после изменений выполните `git pull` в клоне и перезапустите клиент. Для установки изменившихся зависимостей может понадобиться сеть.

Изоляция проходит между профилями. Если в одном клиенте подключено несколько профилей, модель может пользоваться каждым из них.

Отметка Received своя у каждого профиля: сводка по учёбе не прячет элементы от сводки по работе.

## Обновление старой установки

Переход со Study Inbox и ротация мигрированного ключа описаны в [руководстве по обновлению](../docs/migrations.md).

[Основной README](../README.ru.md) · [HTTP API](../docs/api.md)
