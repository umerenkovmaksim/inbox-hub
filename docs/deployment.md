# Deployment

[README](../README.md) · [Configuration](configuration.md) · [Upgrading](migrations.md)

## Processes and data

Two processes share one database:

```bash
uv run inbox-hub collect
uv run inbox-hub serve
```

`collect` is the only process that connects to Telegram. It handles new messages, edits, and deletions through events; `catch_up=True` catches up after downtime. Reconciliation runs at startup and every `TELEGRAM__RECONCILE_INTERVAL_HOURS`, backfilling tracked chats with new messages.

The collector holds `data/telegram.lock`. A second `collect` or `login` using the same session cannot start. Stop the collector before logging in again. `serve` runs the HTTP API with one worker and scheduled LMS jobs.

By default, `data/` holds SQLite data, attachment files, and the account session. Deleted messages receive `deleted_at` and disappear from `/items`, but remain in the database. The original Telegram message representation is stored in `items.raw`.

One-off import of a Telegram Desktop JSON export:

```bash
uv run inbox-hub import-export /path/to/result.json --chat-id -1001234567890
```

Replace the path and fictional ID. Keep attachment paths relative to the export's JSON file.

## Docker Compose

Prepare `.env` using the [configuration guide](configuration.md). The current Compose setup bind-mounts `catalog.yaml`; create the personal file before starting. An empty catalog is enough to begin:

```bash
test -e catalog.yaml || printf '{}\n' > catalog.yaml
docker compose build
docker compose run --rm --no-deps inbox-hub inbox-hub init-db
docker compose run --rm --no-deps inbox-hub inbox-hub login
docker compose up -d
```

For subsequent updates, use `docker compose up -d --build`. The `inbox-hub` service applies migrations before running `serve`. The `collector` service starts after the API's TCP port check succeeds; this check does not diagnose external Sources.

Compose does not publish the API port to the host. For a local reverse proxy or host access, add a personal `compose.override.yaml`:

```yaml
services:
  inbox-hub:
    ports:
      - "127.0.0.1:8000:8000"
```

Run `docker compose up -d` again after creating the override. For remote access, configure HTTPS through a reverse proxy. The [Caddyfile](../deploy/Caddyfile) contains an example domain to replace with your own.

Data is stored in the `university-pz-parser_inbox-data` volume. Its historical name is pinned intentionally so renaming the project directory does not create a new empty volume.

Management commands:

```bash
docker compose exec inbox-hub inbox-hub list-chats
docker compose exec inbox-hub inbox-hub channels list
docker compose logs --tail=100 inbox-hub collector
```

See the [catalog guide](catalog.md) for catalog management. To log in again, stop `collector` first, complete `login`, and start the collector again.

## systemd

The templates assume Linux, the `inboxhub` user, and installation at `/opt/inbox-hub`. Create that user, place the project and `.env` there, install dependencies with `uv sync --locked --no-dev`, and run `init-db` and `login` as `inboxhub`. That user needs write access to `data/`.

Install the units:

```bash
sudo install -m 644 deploy/inbox-hub.service /etc/systemd/system/
sudo install -m 644 deploy/inbox-hub-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inbox-hub inbox-hub-collector
```

For a different path or user, first update `User`, `WorkingDirectory`, `EnvironmentFile`, `ExecStart`, and `ReadWritePaths` in the templates. Configure Caddy with your domain and the local API address.

Use authenticated `GET /health` requests to check Sources. The admin token exposes error details; a client key receives a reduced response.
