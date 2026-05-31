# HTTP Server

fit-pipeline includes a FastAPI HTTP server for triggering pipeline runs over the network. It shares all pipeline logic with the CLI — the same processor chain, the same config, the same delivery layer.

## Starting the Server

```bash
# From the project root
SERVER_SECRET=your_secret_here python server.py

# With full config
SERVER_SECRET=your_secret \
WEBHOOK_DESTINATIONS='[{"url":"https://your-app.example.com/ingest","secret":"your_webhook_secret"}]' \
THRESHOLD_HR=162 RESTING_HR=48 MAX_HR=185 \
python server.py
```

The server listens on `0.0.0.0:8000` by default. Override the port with the `SERVER_PORT` env var.

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

The response contains a `files` array with one entry per processed file. Each entry has `file` and `status`; the full payload is delivered to the configured destinations, not echoed back.

```json
{
  "status": "ok",
  "processed": 1,
  "failed": 0,
  "files": [
    { "file": "sample_run.fit", "status": "ok" }
  ]
}
```

**Processing failure (500):**

If a file fails to parse, process, or deliver, the server returns 500 with the same shape; the failed entry carries an `error` message.

```json
{
  "status": "error",
  "processed": 0,
  "failed": 1,
  "files": [
    { "file": "bad.fit", "status": "error", "error": "FIT decode errors: ..." }
  ]
}
```

**Error responses:**

| Status | Condition |
|---|---|
| 401 | Missing or invalid Authorization header |
| 422 | Body missing `path`, path not found, or not a `.fit` file / empty directory |
| 500 | Processing failed (structured JSON, never a raw traceback) |

Validation errors use a `detail` message:

```json
{
  "detail": "human-readable error message"
}
```

### POST /upload

Upload a binary `.fit` file directly instead of referencing a server-side path. Useful when the caller has the file but no filesystem access to the pipeline host.

**Request:** `multipart/form-data` with a `file` field containing the `.fit` file.

```bash
curl -X POST http://localhost:8000/upload \
  -H "Authorization: Bearer your_secret_here" \
  -F "file=@morning-run.fit"
```

The file is saved to `UPLOAD_DIR`, processed through the pipeline, and moved to `UPLOAD_DIR/completed/` on success. The response mirrors `/process` (a single-entry `files` array). The uploaded filename is reduced to its basename, so it cannot write outside `UPLOAD_DIR`.

**Error responses:**

| Status | Condition |
|---|---|
| 401 | Missing or invalid Authorization header |
| 422 | Upload is not a `.fit` file |
| 503 | `UPLOAD_DIR` is not configured |
| 500 | Processing failed |

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
| `SERVER_PORT` | `8000` | Listen port |
| `UPLOAD_DIR` | (empty) | Directory for `POST /upload` files; required to use that endpoint |

## Testing

The server is tested with FastAPI's `TestClient` — no real HTTP calls are made in the test suite. See `tests/test_server.py`.

```bash
make test
# or
pytest tests/test_server.py -v
```
