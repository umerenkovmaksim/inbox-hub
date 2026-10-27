# Upgrading an existing installation

[README](../README.md) · [Deployment](deployment.md)

Skip these steps for a new installation. Back up the database and files before applying migrations. Run migrations with `uv run inbox-hub init-db`; Docker applies them automatically before starting the API.

## Migrating from Study Inbox

Migration `0002` places existing Channels in the `study` Category and transfers Received marks to the `study` key. That key is created inactive.

1. Apply migrations with `init-db`.
2. Issue a token with `uv run inbox-hub keys rotate study`. The token is printed once.
3. In the MCP configuration, replace `study-inbox-mcp` with `inbox-hub-mcp`, replace the admin token with the issued `ihk_...` token, add `INBOX_PROFILE`, and use the [current configuration](../mcp_adapter/README.md).
4. Restart the AI client.

In Docker, rotate with `docker compose exec inbox-hub inbox-hub keys rotate study`. The old admin token cannot set Received marks: each API key now has independent marks.

## Moving to a separate collector

1. Stop the old `serve` process. It previously connected to Telegram itself; two connections with the same session must not run together.
2. Run `init-db`. Migration `0003` adds `raw`, `deleted_at`, and a dialog snapshot, and advances chat cursors to the last stored `msg_id`. Existing materials are preserved.
3. Start `collect` and `serve` as separate processes. No new `login` is needed; the existing session file is reused.

For systemd, also install `inbox-hub-collector.service`. Docker Compose already defines both processes. Keep the existing data volume's historical name.
