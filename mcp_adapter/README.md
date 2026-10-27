# inbox-hub-mcp

[English](README.md) · [Русский](README.ru.md)

The MCP adapter for Inbox Hub runs locally over stdio and connects to the server over HTTPS.

Tools: `scope`, `get_items`, `mark_received`, `get_item`, `get_file`, and `health`. Every profile has the same tool set.

## Profiles

One package can run multiple times with different **profiles**. A profile is a server-side API key assigned to a set of Categories. The server determines access from the key; the model cannot expand it. The `categories` argument of `get_items` can only narrow a profile's scope. An out-of-scope Category returns 403; an out-of-scope Item or File returns 404.

Create keys on the server from the repository root. Tokens are printed once. [Create the Categories first](../docs/catalog.md):

```bash
uv run inbox-hub keys create personal --category study --category work --category news
uv run inbox-hub keys create study-digest --category study
```

Environment variables:

- `INBOX_URL`: server address.
- `INBOX_TOKEN`: an API key token (`ihk_...`).
- `INBOX_PROFILE`: the MCP server name exposed to the client.
- `INBOX_DOWNLOAD_DIR`: optional; defaults to `~/Downloads/inbox-hub`.

**Keep the admin token (`API__TOKEN`) out of MCP configurations.** The adapter exposes no administrative tools.

## MCP client configuration

Run the adapter directly from GitHub with `uvx`. At each MCP server startup, `--refresh-package` checks for the latest version on `main`, while dependencies (`mcp`, `httpx`) can use the cache. If your server runs a different revision, pin the same commit SHA in the URL instead of `main`. Updates take effect the next time the MCP server starts, such as after restarting the client or starting a scheduled task. A running process does not restart itself.

The repository is public: HTTPS installation requires no GitHub account or SSH key. Install `uv`, Git, and Python 3.12+ on the client machine; the server requires Python 3.14+. GitHub must be reachable at startup when using `--refresh-package`. The examples below use the `mcpServers` configuration format; the configuration file's location depends on the client.

A broad profile can be useful for interactive work where a person supervises the model:

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
      "env": {
        "INBOX_URL": "https://inbox.example.ru",
        "INBOX_TOKEN": "ihk_...personal",
        "INBOX_PROFILE": "inbox"
      }
    }
  }
}
```

For a scheduled task, use a narrow profile and connect only that profile in its configuration. Text in an external channel asking the model to retrieve work materials cannot grant the study key access to work Categories:

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
      "env": {
        "INBOX_URL": "https://inbox.example.ru",
        "INBOX_TOKEN": "ihk_...study-digest",
        "INBOX_PROFILE": "inbox-study"
      }
    }
  }
}
```

### Running from a local clone

To run without fetching GitHub at startup, use a local clone. Install its dependencies once with `uv sync --locked --directory /ABS/PATH/inbox-hub/mcp_adapter`, then configure:

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

Replace the absolute path and placeholder credentials. This setup does not update the adapter automatically. Pull changes into the clone and restart the client to update it; changed dependencies may need network access.

Isolation is between profiles. If a client connects multiple profiles, the model can use all of them. Received marks are independent for each profile: a study briefing does not hide Items from a work briefing.

## Upgrading an older installation

See the [upgrade guide](../docs/migrations.md) for migration from Study Inbox and rotation of the migrated key.

[Main README](../README.md) · [HTTP API](../docs/api.md)
