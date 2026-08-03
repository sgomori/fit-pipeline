# fit-pipeline

A configurable FIT file parsing framework that extracts Garmin activity data, supports custom analytical middleware, and delivers structured JSON to a webhook endpoint.

## What it does

fit-pipeline reads a Garmin FIT file (or a directory of FIT files), extracts activity data using Garmin's official Python SDK, passes the data through a configurable middleware chain, and delivers the result as structured JSON to a webhook endpoint or a local file.

The framework ships with a standard analytics middleware layer that computes training metrics including aerobic decoupling, efficiency factor, cardiac drift, training stress score, and zone distributions. This middleware is opt-in — the core pipeline works without it.

## What it doesn't do

The framework handles parsing and delivery. It does not prescribe what you do with the data between those two steps. That's the middleware layer's job, and you control it.

## Quick start

```bash
# Process a single FIT file
python pipeline.py /path/to/activity.fit

# Process a directory of FIT files
python pipeline.py /path/to/fit_files/

# Dry run — output payload to stdout without POSTing
python pipeline.py /path/to/activity.fit --dry-run

# Write payload to a local JSON file instead of posting
python pipeline.py /path/to/activity.fit --output /path/to/output.json
```

## HTTP endpoint

fit-pipeline can also run as a persistent HTTP service, enabling remote triggering from n8n instances on separate servers or any HTTP client:

```bash
# Start the HTTP server
python server.py

# Trigger processing via HTTP
curl -X POST http://localhost:8000/process \
  -H "Authorization: Bearer your_server_secret" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/import/"}'
```

See `docs/server.md` for full endpoint documentation.

## Configuration

Configuration is via environment variables. A `.env` file is supported for local development.

```bash
# Webhook delivery — JSON array of {url, secret} destinations
WEBHOOK_DESTINATIONS=[{"url":"https://your-app.example.com/webhooks/activity","secret":"your_secret"}]

# Field filtering
EXCLUDE_GPS=true
EXCLUDE_DEVICE_INFO=true

# Streams
INCLUDE_STREAMS=true
STREAM_SAMPLE_RATE=3        # seconds between samples (default: 3)

# Output
DRY_RUN=false
# Path to write JSON output; leave empty to POST. Keep the comment on its own
# line — python-dotenv only strips an inline comment when a value precedes it,
# so `OUTPUT_FILE=  # comment` assigns the comment text as the value.
OUTPUT_FILE=

# Completed-file naming — strftime pattern applied to files moved into
# completed/, using the activity's local start time. The original stem is
# always appended. Empty (default) keeps the received filename. Renaming is
# idempotent — reprocessing a renamed file will not add a second date.
#   "%Y-%m-%d-%H%M"  ->  2026-07-25-1136_463372454903.fit
COMPLETED_FILENAME_FORMAT=

# Set the completed file's mtime to the activity's start instant, so date-based
# sorting in a file manager matches. Independent of the setting above.
COMPLETED_SET_MTIME=false

# Logging
LOG_LEVEL=INFO
LOG_FILE=pipeline.log        # leave empty to log to stdout only
```

## Middleware

The framework provides a base `Processor` class that middleware must subclass. A processor receives the parsed activity data, transforms it, and returns the modified data.

```python
from fit_pipeline import Processor

class MyProcessor(Processor):
    def process(self, data: dict) -> dict:
        # Transform data here
        return data
```

Processors are registered in the pipeline configuration. Multiple processors run in sequence.

The repository includes a `StandardAnalyticsProcessor` that computes the built-in training metrics. Enable it by including it in your processor chain.

See `docs/middleware.md` for the full middleware API reference and `examples/` for example processor implementations.

## Batch processing

When given a directory path, the pipeline processes FIT files in chronological order (by file modification time). For webhook delivery:

- Each file is processed and POSTed sequentially
- The pipeline awaits a 200 response before proceeding to the next file
- Successfully processed files are moved to a `completed/` subdirectory
- Failed files remain in the source directory with an error logged
- The pipeline exits with a non-zero code if any file fails

This makes batch processing safe to restart — if the pipeline is interrupted, already-processed files are in `completed/` and won't be reprocessed.

## Error handling

The pipeline fails loudly. If a FIT file is malformed or unparseable, the pipeline logs the error and exits with a non-zero exit code without POSTing to the webhook. This ensures your webhook consumer never receives partial or invalid data.

For batch processing, a failed file halts the batch. Fix the issue and restart — completed files won't be reprocessed.

## Triggering

fit-pipeline supports two invocation modes:

**CLI** — invoke directly from the command line, a cron job, or any workflow tool that can execute a shell command (n8n Execute Command node, Make, etc.). No persistent process required.

**HTTP endpoint** — run as a persistent FastAPI service and trigger via HTTP POST. Enables remote triggering from n8n instances on separate servers or any HTTP client. See `docs/server.md`.

## Related projects

- [training-insights](link-tbd) — the Rails MCP server and web frontend that consumes this pipeline's webhook output
- [training-insights-n8n](link-tbd) — n8n workflow definitions for automating pipeline execution

## Contributing

See `CONTRIBUTING.md` for contribution guidelines. Bug reports and fixes are welcome. Before submitting significant changes, please open an issue to discuss fit with the project's direction.

## License

To be determined before the repository is made public.
