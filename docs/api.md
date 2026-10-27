# HTTP API

[README](../README.md) · [MCP](../mcp_adapter/README.md)

The local API defaults to `http://127.0.0.1:8000`. Every request, including `/health`, requires `Authorization: Bearer <token>`. Interactive documentation at `/docs`, `/redoc`, and `/openapi.json` is disabled.

## Access

| Credentials | Capabilities |
| --- | --- |
| API key `ihk_...` | Items and Files in assigned Categories, with independent Received marks |
| Admin token `API__TOKEN` | All materials and `/admin` management; no Received marks of its own |

Create API keys through the [CLI or catalog](catalog.md). The `categories` parameter only narrows a key's access. An out-of-scope Category returns 403; an out-of-scope Item or File returns 404. A key without Categories cannot access materials. Keep the admin token out of MCP configurations.

## Items and Files

| Method and path | Purpose |
| --- | --- |
| `GET /items` | Feed for a time window |
| `GET /items/{id}` | Item with full stored File texts |
| `POST /items/receive` | Mark Items Received by the current key |
| `GET /files/{sha256}` | Download an original File |
| `GET /me` | Key name and assigned Categories |
| `GET /health` | Source health; error details are visible only to the admin |

Example request with an API key in `INBOX_TOKEN`:

```bash
curl --get http://127.0.0.1:8000/items \
  -H "Authorization: Bearer $INBOX_TOKEN" \
  --data-urlencode 'from=2026-10-01T00:00:00+03:00' \
  --data-urlencode 'to=2026-10-02T00:00:00+03:00' \
  --data-urlencode 'categories=work'
```

`from` and `to` are required and must include a time zone. The start is inclusive; the end is exclusive. Results include Items published in the window and previously published Items edited during it. Deleted Items are excluded from the list.

Optional parameters:

| Parameter | Behavior |
| --- | --- |
| `categories` | Repeatable: `categories=work&categories=news` |
| `source` | Restrict results to one Source |
| `include_received` | Defaults to `false`; `true` includes previously Received Items |
| `limit` | From 1 to 1000; defaults to 200 |

The response has the shape `{"items": [...], "count": 1}`. `count` is the returned list size, not the total number of matches. There is no cursor pagination. If the limit is reached, narrow the time window or use Received marks to process Items in batches.

An Item includes its ID, Source, Channel title, Categories, kind, text, author, original URL, publication and update timestamps, deadlines, duplicate reference, and attachments. The original URL may be absent. The list endpoint caps each File's text at `FEED_TEXT_LIMIT`; `GET /items/{id}` returns stored text without that additional cap. `text_status` and `text_truncated` describe extraction limitations.

`files` contains current attachments, `removed_files` contains attachments detached on edit, and `skipped_files` describes skipped downloads. Where supported by the Source, `grade` contains an assessment and feedback.

## Received marks

Each API key has its own receipt. It does not mark content as read in the external Source. Editing an Item clears all keys' marks so clients can retrieve the new version.

```bash
curl http://127.0.0.1:8000/items/receive \
  -H "Authorization: Bearer $INBOX_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"ids": [42], "versions": {"42": "2026-10-01T09:00:00Z"}}'
```

Pass the processed Item's `updated_at` in `versions`. If the Item has since changed, the outdated version is not marked Received. The field is optional but prevents missing edits between retrieval and acknowledgement. The MCP adapter sends seen versions automatically. The response is `{"updated": 1}`. Using the admin token to mark Items Received returns 400.

## Administration

The following require `API__TOKEN`:

- `/admin/categories`, `/admin/channels`: Categories and Channels;
- `/admin/telegram/chats`, `/admin/telegram/dialogs`: tracking and dialog snapshots;
- `/admin/keys`: API key creation and management;
- `POST /admin/sync/sdo`: one-off LMS synchronization.

The API does not trigger Telegram collection; a separate collector handles it. For everyday management, use the [CLI](catalog.md).
