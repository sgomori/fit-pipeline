# HTTP Server

fit-pipeline includes a FastAPI HTTP server for triggering pipeline runs over the network. It shares all pipeline logic with the CLI — the same processor chain, the same config, the same delivery layer.

## Starting the Server

```bash
# From the project root
SERVER_SECRET=your_secret_here python server.py

# With full config
SERVER_SECRET=your_secret \
WEBHOOK_URL=https://your-app.example.com/ingest \
WEBHOOK_SECRET=your_webhook_secret \
THRESHOLD_HR=162 RESTING_HR=48 MAX_HR=185 \
python server.py
```

The server listens on `0.0.0.0:8000` by default. Override with `HOST` and `PORT` env vars.

`SERVER_SECRET` is required. The server will not start without it.

## Authentication

All requests must include a Bearer token matching `SERVER_SECRET`:

```
Authorization: Bearer your_secret_here
```

Requests without this header, or with a wrong token, receive `401 Unauthorized`.

## Endpoints

### POST /process

Trigger pipeline processing for a single file or a directory.

**Request body:**

```json
{
  "path": "tests/fixtures/sample_run.fit"
}
```

`path` can be:
- A path to a single `.fit` file
- A path to a directory (processes all `.fit` files in it)

**Successful response (200):**

```json
{
  "status": "ok",
  "processed": 1,
  "failed": 0,
  "results": [
    {
      "file": "sample_run.fit",
      "status": "ok",
      "payload": { ... }
    }
  ]
}
```

**Partial failure response (200):**

When processing a directory, the server returns 200 even if some files fail. Check `failed` and `results[].status` to identify failures.

```json
{
  "status": "partial",
  "processed": 2,
  "failed": 1,
  "results": [
    { "file": "run1.fit", "status": "ok", "payload": { ... } },
    { "file": "bad.fit", "status": "error", "error": "FIT decode errors: ..." }
  ]
}
```

**Error responses:**

| Status | Condition |
|---|---|
| 401 | Missing or invalid Authorization header |
| 422 | Request body missing required `path` field |
| 500 | Unhandled server error |

All error responses are JSON:

```json
{
  "detail": "human-readable error message"
}
```

### GET /health

Returns server status. No authentication required.

```json
{ "status": "ok" }
```

## Configuration

The server uses the same env vars as the CLI. See `.env.example` for the full list.

Server-specific variables:

| Variable | Default | Description |
|---|---|---|
| `SERVER_SECRET` | (required) | Bearer token for API authentication |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port |

## Testing

The server is tested with FastAPI's `TestClient` — no real HTTP calls are made in the test suite. See `tests/test_server.py`.

```bash
make test
# or
pytest tests/test_server.py -v
```
