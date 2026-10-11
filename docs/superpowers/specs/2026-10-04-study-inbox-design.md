# Study Inbox — design

Glossary: [CONTEXT.md](../../../CONTEXT.md). Original AI-generated concept: `study-inbox-project-structure.md` (deviations below take precedence).

## Purpose

Collect every Item from the group's Telegram chats and from the university СДО (Moodle 4.5 at `https://sdo.timacad.ru`) into one store, extract text from Files, and expose a time-windowed feed to Claude through an MCP adapter that marks Items as Received.

## Runtime

- Two processes on a server sharing the SQLite file:
  - `collect` — the only process that talks to Telegram (Telethon, one session, guarded by `data/telegram.lock`).
  - `serve` — FastAPI (1 uvicorn worker) + APScheduler 3.x `AsyncIOScheduler` for СДО. Consumers (API, MCP) read the database only and never import Telethon.
- SQLite via `aiosqlite`, SQLAlchemy 2.x async, Alembic with `render_as_batch=True`.
- Python 3.14, `uv`.
- Bearer token on every HTTP endpoint.
- MCP adapter: separate package `mcp_adapter/`, deps only `mcp>=2` and `httpx`, runs on the laptop over stdio, talks HTTPS to the server.

## Data model

- `channels(id, source, external_id, title, description)` — UNIQUE(source, external_id). Telegram chat (marked id) or СДО course id.
- `items(id, source, external_key, channel_id, kind, title, text, author, url, published_at, edited_at, due_at, available_from, first_seen_at, updated_at, content_hash, edited, previous_text, duplicate_of_id, extra JSON, raw JSON, deleted_at)` — UNIQUE(source, external_key). `raw` is Telethon's `message.to_dict()` (JSON-encoded) for reprocessing; `deleted_at` is a soft delete, rows are never removed.
- `channels.cursor` (Telegram) — highest msg_id processed in the chat, including skipped service messages and stickers.
- `telegram_dialogs(chat_id PK, title, kind, seen_at)` — groups and channels of the account as of the collector's last reconcile; read by `list-chats` and `/admin/telegram/dialogs`.
- `files(sha256 PK, size, mime, original_name, local_path, text, text_status)` — text_status ∈ ok|truncated|needs_ocr|unsupported|failed|skipped.
- `item_files(item_id, file_sha256, remote_id, name, url, attached_at, detached_at)` — PK(item_id, file_sha256). Oversized or unfetchable files are not downloaded; they are kept as metadata in `items.extra["skipped_files"]`.
- `source_health(source PK, last_run_at, last_ok_at, last_error, last_report JSON)`.

## Dedup and edits

1. Item identity: (source, external_key). Telegram: `"{chat_id}:{msg_id}"`. СДО module: `"cm:{cmid}"`, section: `"section:{course_id}:{section_number}"`.
2. Change detection: `content_hash` = sha256 over text, title, due_at, available_from, sorted file `remote_id`s, teacher grade/feedback. Unchanged hash → nothing happens, no downloads.
3. Files deduped by sha256 of content. A replaced file (new sha256) is attached; the old link gets `detached_at`. Old File rows/blobs are kept.
4. On change of an existing Item: `previous_text` = old text, `edited = true`, `edited_at` set, `received_at = NULL`. Own submission status changes are stored in `extra` but excluded from the hash.
5. Text duplicate: normalized text (lowercase, collapsed whitespace, ≥ 40 chars) matches an earlier Item → `duplicate_of_id` = earliest such Item.

## Telegram

- Tracked chats live in the DB catalog (`channels.tracked`, `inbox-hub chats add/remove`); `TELEGRAM__SOURCE_IDS` only seeds it. The collector re-reads the list every minute and backfills newly tracked chats right away. All messages, no keyword filter. Read-only: no read receipts, no sends, no joins.
- Download documents and photos ≤ `max_file_mb` (50), set by `TELEGRAM__DOWNLOAD_KINDS`. Voice/video/round → metadata only. Downloads run one at a time from the collector queue, only for new or changed messages.
- Public Yandex Disk links in text are resolved to files.
- Primary path: `NewMessage`/`MessageEdited`/`MessageDeleted` handlers with `catch_up=True`. Handlers only enqueue; one consumer task writes through `IngestService`. `MessageDeleted` without `chat_id` (private chats, basic groups) is matched by msg_id among tracked non-channel chats.
- Safety net: reconciler at startup and every `reconcile_interval_hours` (4). One `iter_dialogs` pass; `iter_messages(min_id=cursor, reverse=True, wait_time=1)` only for tracked chats whose top message id is above the cursor, strictly one chat at a time. Backfill from `TELEGRAM__BACKFILL_FROM` (2026-09-01) for chats with no cursor.
- No `get_entity`/username resolution, no full-user/channel/participants calls. Titles come from `iter_dialogs` and update entities.
- Client: stable `device_model`/`system_version`/`app_version` from config, `flood_sleep_threshold ≥ 300`, `connection_retries=None`; longer FloodWaits are waited out (`e.seconds`) before one retry.
- `remote_id` for TG media = `"tg:doc:{document.id}"` / `"tg:photo:{photo.id}"`.

## СДО

- Auth: username/password from `.env`. Try `/login/token.php?service=moodle_mobile_app` → REST (`core_course_get_contents`, `mod_assign_get_assignments`). Fallback: form login (`/login/index.php` with `logintoken`) → HTML of `/course/view.php`, module pages, plus AJAX `core_calendar_get_action_events_by_timesort` and `core_course_get_enrolled_courses_by_timeline_classification` with `sesskey`.
- All enrolled courses except `SDO__EXCLUDE_COURSE_IDS`.
- Module kinds: assign (intro, intro attachments, dates, submission status, grade, teacher feedback), resource (file), folder (all files), url (target URL; Yandex Disk resolved), scorm (metadata only), anything else (name + description).
- `remote_id` for СДО files = `ETag` from a `HEAD` on the pluginfile URL (it is the file's sha1), fallback `Last-Modified`, fallback URL.
- `published_at` = earliest of attachment upload times / "Открыто с"; else `first_seen_at`.
- Grades (СДО only): the assign page `.feedback` block is parsed into `{value, max, graded_at, grader, grading, feedback}`; other modules take value/max/feedback from the course grade report (`/grade/report/user/index.php?id=`). The structure is hashed, so a changed grade makes the Item Edited. The course total is a separate Item `course_grade:{course_id}`. The API exposes it as `grade` on `/items` and `/items/{id}`. If the grade report fails to load, the course is skipped for that run.
- Schedule: every 3 hours 07:00–22:00 Europe/Moscow.

## Text extraction

docx, pdf, pptx, xlsx, ipynb, txt/md/csv/py, zip (recursive, depth 2). Runs in `asyncio.to_thread`. PDF with no text layer → `needs_ocr`. Text capped at 200 000 chars → `truncated`.

## HTTP API

- `GET /items?from&to&source&include_received=false&limit=200` — window over `published_at`, plus Edited items whose `edited_at` falls into the window; file texts truncated to 4000 chars.
- `GET /items/{id}` — full item with full file texts.
- `POST /items/receive {"ids": [...]}` → sets `received_at`.
- `GET /files/{sha256}` — raw file.
- `GET /health` — per-source health.
- `POST /admin/sync/sdo` — manual СДО sync (Telegram is never triggered from the API).

## MCP tools

`get_items(from, to, source?, include_received=false)`, `mark_received(ids)`, `get_item(id)`, `get_file(sha256)` (saves to a local dir, returns path), `health()`.

## CLI

`login` (needs the collector stopped), `list-chats` (reads the dialog snapshot), `init-db`, `sync sdo`, `import-export FILE`, `collect`, `serve`, plus `categories`/`channels`/`chats`/`keys`.

## Out of scope

Notes, backups, retention cleanup, Telegram notifications, OCR.

## Categories & access (2026-10-10, Inbox Hub)

The project is renamed to Inbox Hub and is no longer study-only.

- `categories(id, slug, title, description, auto_sources)`, `channel_categories` (M:N). A channel may be in several categories. New channels of a source listed in `auto_sources` join that category on creation.
- `api_keys(name, token_hash, created_at, revoked_at, last_used_at)` + `api_key_categories`. Only sha256 of the token is stored. The scope comes from the credential: every feed query, `GET /items/{id}`, `GET /files/{sha}` and `POST /items/receive` goes through one filter (`services/feed.py::_scoped`). Out-of-scope items/files are 404. The `categories` parameter narrows and is 403 outside the scope. `duplicate_of_id` is hidden when the original is out of scope.
- `receipts(key_id, item_id, received_at)` replaces `items.received_at`. Edits delete all receipts of the item.
- `API__TOKEN` is the admin principal: unrestricted, the only one allowed on `/admin` (categories, channels, tracked chats, keys, Telegram dialogs, sync). The MCP adapter has no admin tools.
- Telegram chats are `channels.tracked` rows managed by `inbox-hub chats add/remove`. Live handlers have no `chats=` filter and check a 60 s cache instead. `TELEGRAM__SOURCE_IDS` only seeds unknown chats.
- MCP: one package, launched per profile (`INBOX_TOKEN`, `INBOX_PROFILE`). Isolation is between keys, so an unattended scheduled task gets a narrow key and only that profile in its config.
- Migration 0002 puts existing channels into `study` (auto source `sdo`) and moves Received marks to an inactive `study` key (`inbox-hub keys rotate study` activates it).
