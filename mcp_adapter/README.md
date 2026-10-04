# study-inbox-mcp

MCP-адаптер к серверу Study Inbox. Работает на ноутбуке по stdio и ходит на сервер по HTTPS.

Инструменты: `get_items`, `mark_received`, `get_item`, `get_file`, `health`.

Переменные окружения: `INBOX_URL`, `INBOX_TOKEN`, необязательно `INBOX_DOWNLOAD_DIR` (по умолчанию `~/Downloads/study-inbox`).

Конфигурация Claude:

```json
{
  "mcpServers": {
    "study-inbox": {
      "command": "uv",
      "args": ["run", "--directory", "/ABS/PATH/university-pz-parser/mcp_adapter", "study-inbox-mcp"],
      "env": {"INBOX_URL": "https://inbox.example.ru", "INBOX_TOKEN": "..."}
    }
  }
}
```
