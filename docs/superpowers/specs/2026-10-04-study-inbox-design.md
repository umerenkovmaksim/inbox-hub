# Study Inbox — design

Glossary: [CONTEXT.md](../../../CONTEXT.md). Original AI-generated concept: `study-inbox-project-structure.md` (deviations below take precedence).

## Purpose

Collect every Item from the group's Telegram chats and from the university СДО (Moodle 4.5 at `https://sdo.timacad.ru`) into one store, extract text from Files, and expose a time-windowed feed to Claude through an MCP adapter that marks Items as Received.

## Runtime

- One asyncio process on a server: FastAPI (1 uvicorn worker) + Telethon client + APScheduler 3.x `AsyncIOScheduler`.
- SQLite via `aiosqlite`, SQLAlchemy 2.x async, Alembic with `render_as_batch=True`.
- Python 3.14, `uv`.
- Bearer token on every HTTP endpoint.
- MCP adapter: separate package `mcp_adapter/`, deps only `mcp>=2` and `httpx`, runs on the laptop over stdio, talks HTTPS to the server.

## Data model

- `channels(id, source, external_id, title, description)` — UNIQUE(source, external_id). Telegram chat (marked id) or СДО course id.
- `items(id, source, external_key, channel_id, kind, title, text, author, url, published_at, edited_at, due_at, available_from, first_seen_at, updated_at, content_hash, edited, previous_text, duplicate_of_id, received_at, extra JSON)` — UNIQUE(source, external_key).
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

- Chats from config (`TELEGRAM__SOURCE_IDS`). All messages, no keyword filter.
- Download documents and photos ≤ `max_file_mb` (50). Voice/video/round → metadata only.
- Public Yandex Disk links in text are resolved to files.
- Live `NewMessage`/`MessageEdited` handlers + catch-up `iter_messages(min_id=last_seen)` at startup and hourly + edit recheck of the last 3 days. Backfill from `TELEGRAM__BACKFILL_FROM` (2026-09-01).
- `remote_id` for TG media = `"tg:doc:{document.id}"` / `"tg:photo:{photo.id}"`.

## СДО

- Auth: username/password from `.env`. Try `/login/token.php?service=moodle_mobile_app` → REST (`core_course_get_contents`, `mod_assign_get_assignments`). Fallback: form login (`/login/index.php` with `logintoken`) → HTML of `/course/view.php`, module pages, plus AJAX `core_calendar_get_action_events_by_timesort` and `core_course_get_enrolled_courses_by_timeline_classification` with `sesskey`.
- All enrolled courses except `SDO__EXCLUDE_COURSE_IDS`.
- Module kinds: assign (intro, intro attachments, dates, submission status, grade, teacher feedback), resource (file), folder (all files), url (target URL; Yandex Disk resolved), scorm (metadata only), anything else (name + description).
- `remote_id` for СДО files = `ETag` from a `HEAD` on the pluginfile URL (it is the file's sha1), fallback `Last-Modified`, fallback URL.
- `published_at` = earliest of attachment upload times / "Открыто с"; else `first_seen_at`.
- Schedule: every 3 hours 07:00–22:00 Europe/Moscow.

## Text extraction

docx, pdf, pptx, xlsx, ipynb, txt/md/csv/py, zip (recursive, depth 2). Runs in `asyncio.to_thread`. PDF with no text layer → `needs_ocr`. Text capped at 200 000 chars → `truncated`.

## HTTP API

- `GET /items?from&to&source&include_received=false&limit=200` — window over `published_at`, plus Edited items whose `edited_at` falls into the window; file texts truncated to 4000 chars.
- `GET /items/{id}` — full item with full file texts.
- `POST /items/receive {"ids": [...]}` → sets `received_at`.
- `GET /files/{sha256}` — raw file.
- `GET /health` — per-source health.
- `POST /admin/sync/{source}` — manual sync.

## MCP tools

`get_items(from, to, source?, include_received=false)`, `mark_received(ids)`, `get_item(id)`, `get_file(sha256)` (saves to a local dir, returns path), `health()`.

## CLI

`login`, `list-chats`, `init-db`, `sync telegram|sdo`, `import-export FILE`, `serve`.

## Out of scope

Notes, backups, retention cleanup, Telegram notifications, OCR.
