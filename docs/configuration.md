# Configuration

[README](../README.md) · [Deployment](deployment.md)

Run local commands from the repository root, which contains `.env` and `alembic.ini`. Start with [.env.example](../.env.example).

## Required settings

The current configuration requires both `TELEGRAM__*` and `SDO__*` credentials, even if your first use case involves only one source. There are no individual integration-disable flags yet.

| Variable | Value |
| --- | --- |
| `TELEGRAM__API_ID`, `TELEGRAM__API_HASH` | Application credentials from [my.telegram.org](https://my.telegram.org) |
| `SDO__USERNAME`, `SDO__PASSWORD` | University LMS credentials |
| `API__TOKEN` | A random admin token of at least 24 characters |

Generate an admin token locally:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the result into `API__TOKEN` in `.env`. The API refuses to start with `change-me`. For MCP, create a separate API key scoped to the required categories; keep the admin token on the server.

## Source settings

### Telegram

Run `uv run inbox-hub login` once, stopping the collector first if it is already running. The session is saved in `data/telegram.session`.

- Select chats with `inbox-hub chats add` or the [catalog](catalog.md). `TELEGRAM__SOURCE_IDS` is a legacy bootstrap mechanism.
- `TELEGRAM__BACKFILL_FROM` sets the history start date for newly tracked chats. The example uses September 1, 2026; adjust it before tracking chats.
- Set `TELEGRAM__DEVICE_MODEL`, `TELEGRAM__SYSTEM_VERSION`, and `TELEGRAM__APP_VERSION` once and keep them stable between runs.
- `TELEGRAM__RECONCILE_INTERVAL_HOURS` sets the reconciliation interval, defaulting to 4 hours. New messages and changes arrive through events.
- `TELEGRAM__DOWNLOAD_KINDS` selects media to download: documents and photos by default. Downloads run sequentially.

### University LMS

The integration is adapted to `https://sdo.timacad.ru`. Changing the URL alone does not guarantee compatibility with an arbitrary Moodle installation.

- `SDO__BACKEND=html` uses HTML parsing, the default.
- `SDO__BACKEND=auto` tries the mobile REST API and falls back to HTML if it cannot obtain a token.
- `SDO__EXCLUDE_COURSE_IDS` is a JSON array of excluded course IDs.
- Synchronization is scheduled hourly at `:20`, from 05:20 through 22:20 in the process's local time zone. For a one-off run, use `uv run inbox-hub sync sdo`.

Teacher assessments appear in the Item's `grade` field. A course total is a separate Item with `kind=course_grade`. In REST mode, grades are available only for assignments.

## Storage and text extraction

Defaults are `DATABASE__URL=sqlite+aiosqlite:///data/inbox.db` and `DATA_DIR=data`. When changing paths, keep session locations and deployment settings consistent with them.

| Setting | Default | Purpose |
| --- | --- | --- |
| `API__HOST` | `127.0.0.1` | Local API bind address |
| `API__PORT` | `8000` | API port |
| `MAX_FILE_MB` | `50` | Maximum downloaded file size |
| `FEED_TEXT_LIMIT` | `4000` | Per-file text limit in the Item list |

Text extraction supports PDF, DOCX, PPTX, XLSX, IPYNB, text formats, and ZIP archives with up to two levels of nesting. Extracted text is capped at 200,000 characters; full text in the API means the stored extraction within that cap. A PDF without a text layer receives `needs_ocr`. Unsupported and damaged files receive the corresponding status instead of text.

`.env`, `data/`, session files, and the personal `catalog.yaml` are excluded from Git. Configuration examples contain fictional values only.
