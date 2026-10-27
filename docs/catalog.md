# Categories, channels, and keys

[README](../README.md) · [HTTP API](api.md)

A Category groups Channels; a Channel can belong to multiple Categories. An API key can access materials only from its assigned Categories. A Channel without Categories is still collected but is accessible only to the admin token.

## CLI management

Run these examples from the repository root. Chat IDs are fictional; create Categories before assigning Channels or keys to them.

```bash
uv run inbox-hub categories create work --title "Work"
uv run inbox-hub categories create news --title "News"
uv run inbox-hub list-chats
uv run inbox-hub chats add -1001234567890 --category work
uv run inbox-hub categories add-channel news telegram:-1001234567890
uv run inbox-hub channels list
uv run inbox-hub keys create work-briefing --category work
```

`list-chats` and `GET /admin/telegram/dialogs` return the group and channel snapshot from the collector's last reconciliation, without contacting Telegram. The list is empty until the collector's first run. A newly tracked chat is picked up within a minute, with history starting at `TELEGRAM__BACKFILL_FROM`.

The key's token is printed once; only its sha256 hash is stored. Manage its access and lifecycle with:

```bash
uv run inbox-hub keys grant work-briefing news
uv run inbox-hub keys ungrant work-briefing news
uv run inbox-hub keys rotate work-briefing
uv run inbox-hub keys revoke work-briefing
```

Rotation issues a new token that must replace the client's existing token. Revocation disables access. Removing a tracked chat with `chats remove CHAT_ID` keeps collected Items.

A Category can automatically include new Channels from a Source. For example: `uv run inbox-hub categories create study --title "Study" --auto-source sdo`.

These commands access the database directly and can run alongside the active processes. In Docker, use `docker compose exec inbox-hub inbox-hub ...`.

## Declarative catalog

[catalog.example.yaml](../catalog.example.yaml) describes Categories, Channels, tracked chats, and key scopes. Copy it to `catalog.yaml` and replace fictional IDs with your own. The personal file is excluded from Git.

```bash
uv run inbox-hub catalog export -o catalog.yaml
uv run inbox-hub catalog plan
uv run inbox-hub catalog apply
```

`export` writes the current state, including Channel titles as comments. Preserve any manual edits before exporting over them. `plan` previews changes without writing to the database. `apply` displays the plan, asks for confirmation, and applies changes in one transaction.

- Without `--prune`, entries are added and updated; potential removals are listed separately. Explicit `exclude` and `telegram.paused` still exclude Channels and pause collection, respectively.
- With `--prune`, absent Categories, Channel memberships, key grants, and chat tracking are removed. First run `uv run inbox-hub catalog plan --prune`: a slug typo can result in a Category being removed.
- A Telegram chat listed in any Category's `channels` is collected. `telegram.track` selects chats without Categories; `telegram.paused` stops collection while keeping Category membership.
- Automatically included Channels from `auto_sources` are not pruned. Use `exclude` to remove one.
- An LMS course not yet discovered by synchronization is skipped with a warning. Apply the catalog again after synchronization.
- Tokens are never stored in YAML. `apply` prints a new key's token once. Keys absent from the file are not revoked; rotation and revocation remain separate commands.

In Docker, pass a local file through stdin:

```bash
docker compose exec -T inbox-hub inbox-hub catalog plan -f - < catalog.yaml
docker compose exec -T inbox-hub inbox-hub catalog apply -f - --yes < catalog.yaml
```

`apply` requires `--yes` when reading from stdin. The current Compose setup also bind-mounts the catalog at `/app/catalog.yaml`, so the host file must exist before starting the container.
