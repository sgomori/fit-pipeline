# fit-pipeline — Technical Specification

This document captures the architecture, design decisions, and rationale for fit-pipeline. It's the primary reference for working in this codebase.

## Project identity

**Name:** fit-pipeline
**Description:** A configurable FIT file parsing framework that extracts Garmin activity data, supports custom analytical middleware, and delivers structured JSON to a webhook endpoint.
**Goal:** Portfolio project demonstrating Python data engineering capability, framework design, and sports science domain knowledge. Also serves as the data ingestion layer for the training-insights Rails application.
**Primary dependency:** Garmin's official Python FIT SDK — https://github.com/garmin/fit-python-sdk

## Architectural principles

**The framework does two things: parse and deliver.** Everything between those two steps is middleware. The core pipeline has no opinions about what fields matter, what metrics to compute, or what shape the output takes. Those are middleware concerns.

**Fail loudly.** A malformed FIT file, a failed webhook POST, or a missing required configuration causes the pipeline to exit with a non-zero code and log a clear error. Silent failures are worse than loud ones for a data pipeline.

**Middleware is opt-in.** The standard analytics middleware ships with the framework but must be explicitly included in the processor chain. The core pipeline is useful without it.

**Batch processing is safe to restart.** Successfully processed files are moved to a completed directory. If the pipeline is interrupted mid-batch, a restart picks up where it left off.

**The webhook contract is versioned.** Every payload includes a schema_version field. Consumers can use this to handle payload evolution gracefully.

## Project structure

```
fit-pipeline/
  pipeline.py               # CLI entry point
  server.py                 # HTTP endpoint entry point (FastAPI)
  fit_pipeline/
    __init__.py
    parser.py               # FIT file parsing via Garmin SDK
    processor.py            # Base Processor class (middleware interface)
    delivery.py             # Webhook and file output
    config.py               # Configuration loading from env
    batch.py                # Batch processing logic
    server.py               # FastAPI app and route definitions
    middleware/
      __init__.py
      standard_analytics.py # StandardAnalyticsProcessor (opt-in)
      field_filter.py       # FieldFilterProcessor (example)
  examples/
    custom_processor.py     # Example middleware implementation
  tests/
    fixtures/               # Anonymized FIT files for testing
    test_parser.py
    test_standard_analytics.py
    test_delivery.py
    test_batch.py
    test_server.py
  docs/
    middleware.md           # Middleware API reference
    payload_schema.md       # Webhook payload schema documentation
    server.md               # HTTP endpoint documentation
  .env.example              # Configuration template
  CONTRIBUTING.md
  README.md
```

## FIT parsing layer

### Dependency

Garmin's official Python FIT SDK (`garmin-fit-sdk` on PyPI). Chosen over third-party alternatives (fitparse, etc.) for:

- Long-term maintenance by Garmin directly
- Correctness on Garmin-specific FIT dialect extensions
- Official support channel for bug reports

The parser wraps the SDK and exposes a clean internal data structure. The rest of the pipeline never touches the SDK directly — all SDK interaction is isolated in `parser.py`.

### What gets extracted

The parser extracts data from these FIT message types:

- **session** — activity summary (distance, duration, start time, average metrics, max metrics)
- **record** — per-timestamp data points (heart rate, pace, cadence, altitude, power)
- **lap** — lap summaries if present
- **event** — start/stop events for moving time calculation

### Field filtering

Field exclusion is configurable via environment variables. The following are excluded by default and can be re-enabled via configuration:

- GPS coordinates (lat/lon) — excluded by default (`EXCLUDE_GPS=true`)
- Device information (serial number, software version, hardware version) — excluded by default (`EXCLUDE_DEVICE_INFO=true`)
- Any other field can be excluded via `EXCLUDE_FIELDS=field_name_1,field_name_2`

Field inclusion decisions for a specific deployment (e.g., the Training Insights implementation) are handled at the middleware layer, not the parser layer. The parser extracts everything not explicitly excluded; middleware can further filter.

### Stream sampling

Time-series streams (heart rate, pace, cadence, altitude) are sampled at a configurable rate rather than every record message.

- Default: every 3 seconds (`STREAM_SAMPLE_RATE=3`)
- A two-hour run at 1Hz produces 7,200 data points per stream. At 3-second sampling, this becomes 2,400 — a 67% reduction with minimal loss of analytical fidelity for the metrics being computed.
- Set `STREAM_SAMPLE_RATE=1` for 1Hz (full resolution)
- Sampling is time-based (every N seconds of elapsed time), not index-based

## Middleware layer

### Philosophy

The middleware layer is where domain knowledge lives. The framework provides the infrastructure; middleware provides the intelligence.

### Base Processor class

All middleware must subclass `Processor`:

```python
class Processor:
    def __init__(self, config: dict):
        self.config = config

    def process(self, data: dict) -> dict:
        """
        Receive parsed activity data, return transformed data.
        Must return a dict. Returning None is an error.
        Raising an exception halts the pipeline.
        """
        raise NotImplementedError
```

Processors are registered as a list and executed in sequence. Each processor receives the output of the previous one. The final processor's output is delivered to the configured output target.

### Registering processors

Processors are registered in `processors.py` at the project root as `PROCESSOR_CHAIN`, a list of Processor subclasses. Both the CLI and HTTP server import from this module. This is a Python config file — no dynamic discovery, no YAML parsing, full IDE support and type safety.

### StandardAnalyticsProcessor

Ships with the framework as opt-in middleware. Computes the following metrics from the parsed stream data:

All metrics are written to `data["computed_metrics"]`. Missing required streams result in null values — not errors.

**aerobic_decoupling_pct**
Cardiovascular drift using the speed/HR ratio (TrainingPeaks Pa:HR convention). Records are split at the elapsed-time midpoint. `eff = avg_speed_m_per_min / avg_hr` per half; decoupling = `(eff_h1 - eff_h2) / eff_h1 × 100`. Positive = HR drifted up relative to speed. Under 5% indicates aerobic efficiency. Requires pace + heart_rate streams.

**efficiency_factor**
`avg_speed_m_per_min / avg_heart_rate`, where average speed is the arithmetic (time-weighted) mean of the per-record speed samples — not the harmonic mean of pace. No grade adjustment. Stopped/near-stopped samples (≤ 0.5 m/s) are excluded. Expected range ~1.2–1.8 for trained runners. Requires speed + heart_rate streams.

**cardiac_drift_bpm**
`Q4_avg_hr - Q1_avg_hr` (first and last 25% of heart_rate records). Requires ≥ 8 records. Note: pace is not controlled; elevation and pacing changes affect the value.

**tss_score (hrTSS)**
`IF = avg_heart_rate / LTHR`; `hrTSS = (duration_seconds × IF²) / 3600 × 100`. LTHR resolved per-activity: FIT `zones_target.threshold_heart_rate` → `THRESHOLD_HR` env var → null with WARNING logged. hrTSS uses *average* HR, so it cannot reward variability — see `rtss_score` for the pace-native score.

**rtss_score (rTSS)**
Run TSS from Normalized Graded Pace. Grade-adjusted speed is smoothed over a 30-second rolling window, then normalized via the 4th-power mean (NGP). `IF = NGP_speed / threshold_speed` (threshold from `THRESHOLD_PACE`, s/km); `rTSS = (duration_seconds × IF²) / 3600 × 100`. Because NGP weights surges, rTSS captures intensity distribution that hrTSS averages away. Null when `THRESHOLD_PACE` is unset (hrTSS remains the HR-only fallback). Requires speed + altitude + distance streams.

**pace_cv**
Coefficient of variation of pace: `std(pace_s_per_km) / mean(pace_s_per_km)` (stopped samples excluded). Lower = more consistent pacing. Note: this is pace CV, not the Coggan Variability Index (Normalized Power / Average Power). Requires pace stream.

**hr_zone_distribution**
Time-in-zone percentages using LTHR-based zones (Joe Friel's running zones, compressed to 5 buckets). Default boundaries: Z1 <85%, Z2 85–89%, Z3 90–99%, Z4 100–106%, Z5 >106% of LTHR. Override with fixed BPM via `HR_ZONE_1` through `HR_ZONE_5`. Null if LTHR unavailable.

**pace_zone_distribution**
Time in four zones (easy, moderate, threshold, hard) as percentages. Boundaries from `PACE_ZONE_EASY`, `PACE_ZONE_MODERATE`, `PACE_ZONE_THRESHOLD` (s/km). Null if none configured.

**trimp (Banister Training Impulse)**
`hrr = (avg_hr - resting_hr) / (max_hr - resting_hr)`; `TRIMP = duration_min × hrr × c1 × e^(c2 × hrr)`. Male coefficients (default): c1=0.64, c2=1.92. Female (`TRIMP_GENDER=female`): c1=0.86, c2=1.67. `max_hr` resolution: `MAX_HR` config → FIT `zones_target.max_heart_rate` (athlete profile max; the session peak is not used). Null if `RESTING_HR` not configured or no max HR is available.

**avg_grade_adjusted_pace_per_km / grade_adjusted_efficiency_factor**
Grade-Adjusted Pace normalizes pace for elevation. Per-record: `grade_pct = alt_diff_m / dist_diff_m × 100`; uphill factor `1 + 0.033 × grade_pct`, downhill `1 - 0.018 × |grade_pct|` capped at −15%; the grade-adjusted (flat-equivalent) speed is `speed × factor`. The summary averages those grade-adjusted *speeds* arithmetically (stopped samples excluded), reports the equivalent pace, and `grade_adjusted_efficiency_factor = avg_gap_speed_m_per_min / avg_hr`. Polynomial is a Strava-style approximation. Requires speed + altitude + distance streams.

### FieldFilterProcessor

A simple demonstration processor included in the framework. Takes a list of field names to include or exclude from the payload. Shows the middleware interface in action without domain complexity.

### Example processor

`examples/custom_processor.py` provides a documented example of a custom processor implementation — a starting point for developers building their own middleware.

## Delivery layer

### Webhook delivery

Primary output target. Delivers the final processed payload as a JSON POST request to one or more destinations.

Destinations are configured via `WEBHOOK_DESTINATIONS`, a JSON array of `{url, secret}` objects. Each destination authenticates with its own bearer token (host and port are part of the URL):

```
POST <destination.url>
Authorization: Bearer <destination.secret>
Content-Type: application/json
```

**Multiple destinations:** The same payload is delivered to every destination. All destinations are attempted on each run — a single unreachable endpoint does not skip the others — and any failures are aggregated into one error.

**Retry behavior:** Per destination, on a non-200 response or connection failure the pipeline retries once after a short delay. If delivery to any destination still fails, the pipeline exits with a non-zero code. No indefinite retry loop — that's the workflow tool's responsibility.

**Batch behavior:** In batch mode, the pipeline awaits successful delivery before processing the next file. A delivery failure halts the batch; the failed file remains in the source directory, while successfully processed files have already been moved to `completed/`.

**Partial-failure caveat:** With multiple destinations, if some succeed and one fails the run is still treated as failed, so a batch retry re-delivers to the destinations that already succeeded. Receivers should be idempotent on the activity (e.g. dedupe by `file` + `schema_version`).

### File output

Alternative output target. Writes the processed payload as formatted JSON to a specified file path (`OUTPUT_FILE` environment variable or `--output` CLI argument). Useful for development, testing, and consumers that read from the filesystem rather than HTTP.

### Dry run

When `DRY_RUN=true` or `--dry-run` is passed, the pipeline parses, processes, and formats the payload but does not POST or write to a file. The payload is written to stdout. Useful for inspecting output without a live consumer.

## Webhook payload schema

Every payload includes a `schema_version` field. The current version is `"1.1"`. Consumers should check this field when handling payloads to support graceful evolution.

```json
{
  "schema_version": "1.1",
  "source": "garmin_fit",
  "file": "2024-03-15-morning-run.fit",
  "processed_at": "2024-03-15T09:23:41Z",
  "activity": {
    "started_at": "2024-03-15T07:00:00Z",
    "type": "run",
    "distance_meters": 15240,
    "duration_seconds": 4823,
    "elevation_gain_meters": 187,
    "average_pace_per_km": 316,
    "average_heart_rate": 148,
    "max_heart_rate": 171,
    "average_cadence": 174,
    "temperature_celsius": 8
  },
  "computed_metrics": {
    "aerobic_decoupling_pct": 3.2,
    "efficiency_factor": 1.48,
    "cardiac_drift_bpm": 11,
    "tss_score": 87,
    "pace_cv": 0.04,
    "hr_zone_distribution": {
      "zone_1": 8,
      "zone_2": 34,
      "zone_3": 28,
      "zone_4": 22,
      "zone_5": 8
    },
    "pace_zone_distribution": {
      "easy": 31,
      "moderate": 28,
      "threshold": 26,
      "hard": 15
    }
  },
  "streams": {
    "heart_rate": [134, 136, 138, 141],
    "pace": [320, 318, 315, 312],
    "cadence": [172, 174, 174, 176],
    "altitude": [48.2, 48.4, 48.9, 49.3]
  }
}
```

`computed_metrics` is present only when StandardAnalyticsProcessor (or equivalent) is in the processor chain. `streams` is present only when `INCLUDE_STREAMS=true`. Both keys are absent rather than null when not included.

Full schema documentation with field descriptions and units is maintained in `docs/payload_schema.md`.

## Batch processing

### Single file vs directory

The pipeline accepts either a single FIT file path or a directory path. When given a directory:

- All `.fit` files in the directory are collected and sorted chronologically by file modification time
- Files are processed sequentially, one at a time
- Each file is processed fully (parse → middleware chain → delivery) before the next begins

### Completed file management

After a file is successfully processed and delivered (200 response received or output file written):

- The file is moved to a `completed/` subdirectory within the source directory
- The subdirectory is created if it does not exist
- The move is atomic where the OS supports it

Files that fail processing remain in the source directory. The failure is logged with the filename and error. In batch mode, a failed file halts the batch — subsequent files are not processed.

### Restart safety

Because completed files are moved before the next file is attempted, a batch is safe to restart after interruption. Re-running the pipeline on the source directory will find only unprocessed files.

### n8n integration note

When triggered by n8n or a similar workflow tool, n8n is responsible for moving FIT files from the intake location (e.g., Google Drive) to the server directory before invoking the pipeline. The pipeline processes whatever is in the directory at invocation time. n8n can be configured to receive the pipeline's exit code and send a notification on failure.

## HTTP endpoint

In addition to CLI invocation, fit-pipeline exposes a minimal FastAPI HTTP endpoint. This enables remote triggering from n8n instances on different servers, other workflow tools, or any HTTP client — without requiring CLI access to the machine running the pipeline.

### Endpoint

```
POST /process
Authorization: Bearer <SERVER_SECRET>
Content-Type: application/json
```

**Request body:**

```json
{
  "path": "/path/to/import/directory"
}
```

Or for a single file:

```json
{
  "path": "/path/to/activity.fit"
}
```

**Response (200 OK):**

```json
{
  "status": "ok",
  "processed": 2,
  "failed": 0,
  "files": [
    {"file": "morning-run.fit", "status": "ok"},
    {"file": "tuesday-tempo.fit", "status": "ok"}
  ]
}
```

Each entry in `files` carries `file` and `status`; failed entries add an `error` message.

**Response (422 Unprocessable Entity):** Path not found, not a `.fit` file, or no FIT files present.
**Response (401 Unauthorized):** Missing or invalid Bearer token.
**Response (500 Internal Server Error):** Processing failed — structured JSON, never a raw traceback.

### Upload endpoint

```
POST /upload
Authorization: Bearer <SERVER_SECRET>
Content-Type: multipart/form-data
```

Accepts a binary `.fit` file uploaded directly (form field `file`), rather than a path the server can already see. The file is saved to `UPLOAD_DIR`, run through the same pipeline, and moved to `UPLOAD_DIR/completed/` on success. The response uses the same shape as `/process` (a single-entry `files` array).

The client-supplied filename is reduced to its basename before use, so a crafted name cannot write outside `UPLOAD_DIR`.

**Response (503 Service Unavailable):** `UPLOAD_DIR` is not configured.
**Response (422 Unprocessable Entity):** Upload is not a `.fit` file.
**Response (401 / 500):** As for `/process`.

Note: auth is enforced by the route dependency before this handler writes anything, but FastAPI buffers the multipart body before dependencies resolve — so auth gates the pipeline's own filesystem writes, not the framework's request buffering.

### Authentication

A separate secret (`SERVER_SECRET` environment variable) authenticates requests to the HTTP endpoint. This is distinct from the per-destination webhook secrets in `WEBHOOK_DESTINATIONS` (which authenticate the pipeline's outgoing calls). Both `SERVER_SECRET` and at least one webhook destination are required when running the server for live delivery.

### Starting the server

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

Or via the entry point:

```bash
python server.py
```

The server uses the same configuration (`.env` file or environment variables) as the CLI.

### Deployment note

The HTTP endpoint enables n8n and the Python pipeline to run on separate servers. n8n POSTs to the pipeline's endpoint instead of using Execute Command. The pipeline must be deployed as a persistent process (via systemd, Docker, or a managed platform like Fly.io or Railway) for this to work. For same-server deployments, the CLI invocation via Execute Command is simpler and does not require the server to be running.

### Testing the server

```bash
# Start the server in dry-run mode to inspect without delivering
DRY_RUN=true python server.py

# Test with curl
curl -X POST http://localhost:8000/process \
  -H "Authorization: Bearer your_server_secret" \
  -H "Content-Type: application/json" \
  -d '{"path": "/path/to/import/"}'
```

## Error handling

**Malformed FIT file:** Log the error with file path and exception detail. Exit with code 1. Do not POST.

**Missing required configuration:** Log which variables are missing. Exit with code 1 before attempting any file processing.

**Webhook non-200 response:** Log the status code and response body. Retry once after 2 seconds. If retry fails, exit with code 1.

**Middleware exception:** Log the processor class name and exception. Exit with code 1. The partially-processed payload is not delivered.

**File move failure (post-processing):** Log the error. The file was successfully processed and delivered. This is a warning, not a fatal error — continue batch if applicable.

All errors are logged to stderr and to the log file if configured.

## Configuration reference

All configuration is via environment variables. A `.env` file is loaded automatically if present (via python-dotenv).

| Variable | Default | Description |
|---|---|---|
| WEBHOOK_DESTINATIONS | required | JSON array of `{url, secret}` objects; payload is delivered to each, authenticating with that destination's own secret |
| SERVER_SECRET | required if using HTTP endpoint | Bearer token for incoming HTTP endpoint authentication |
| SERVER_PORT | 8000 | Port for the HTTP endpoint server |
| UPLOAD_DIR | (empty) | Directory for files received via `POST /upload`; required to use that endpoint |
| EXCLUDE_GPS | true | Exclude GPS coordinates from output |
| EXCLUDE_DEVICE_INFO | true | Exclude device serial/version fields |
| EXCLUDE_FIELDS | (empty) | Comma-separated additional fields to exclude |
| INCLUDE_STREAMS | true | Include time-series stream data in payload |
| COMPLETED_FILENAME_FORMAT | _(empty)_ | strftime pattern for renaming files moved to `completed/`. Empty means keep the received name. |
| COMPLETED_SET_MTIME | `false` | Set a completed file's mtime to the activity's start instant (from `started_at`, UTC). Independent of the naming pattern. |
| STREAM_SAMPLE_RATE | 3 | Stream sampling interval in seconds |
| DRY_RUN | false | Parse and process without delivering |
| OUTPUT_FILE | (empty) | Write payload to file instead of posting |
| LOG_LEVEL | INFO | Logging level (DEBUG, INFO, WARNING, ERROR) |
| LOG_FILE | (empty) | Log file path; logs to stdout if empty |
| THRESHOLD_HR | (empty) | Static LTHR fallback; per-activity FIT value takes priority |
| MAX_HR | (empty) | Max HR for TRIMP; overrides the FIT profile max (zones_target) |
| RESTING_HR | (empty) | Resting HR for TRIMP; null TRIMP if absent |
| TRIMP_GENDER | male | Banister TRIMP coefficient set (male/female) |
| HR_ZONE_1 through HR_ZONE_5 | (derived) | HR zone upper boundaries in BPM; overrides LTHR % calculation |
| PACE_ZONE_EASY | (empty) | Upper boundary of easy pace zone (s/km) |
| PACE_ZONE_MODERATE | (empty) | Upper boundary of moderate pace zone (s/km) |
| PACE_ZONE_THRESHOLD | (empty) | Upper boundary of threshold/hard boundary (s/km) |
| THRESHOLD_PACE | (empty) | Functional threshold pace (s/km) for rTSS / NGP; null rtss_score if absent |

## Testing

**Framework:** pytest

**Test fixtures:** Anonymized real FIT files. A sample run file is committed to `tests/fixtures/` with GPS coordinates removed, timestamps normalized, and other identifying data modified while preserving the analytical characteristics of the data (stream patterns, metric relationships). Known expected outputs for these fixtures are committed alongside them.

**What's tested:**
- Parser correctly extracts fields from known FIT files
- StandardAnalyticsProcessor produces expected metric values for known inputs
- Field filtering correctly includes/excludes configured fields
- Batch processing moves files and handles failures correctly
- Delivery layer correctly formats and sends the webhook request
- Schema version is present in all payloads
- Dry-run mode does not deliver
- HTTP endpoint authenticates correctly and returns expected responses
- HTTP endpoint error responses for missing path, invalid path, auth failure

**What's not tested:**
- The Garmin SDK itself — trust the upstream library
- Network conditions — mock the HTTP layer in delivery tests

## Open implementation decisions

Unresolved — surface before deciding silently:

- Handling of non-running activity types (cycling, swimming) — currently logs a WARNING and continues
- Stream data precision — pace values stored as floats (s/km); open to rounding policy change
- Whether `completed/` directory path is configurable or always a subdirectory of the source

**Resolved** (do not reopen):
- Processor registration: `processors.py` Python config module
- LTHR source: per-activity FIT `zones_target` → `THRESHOLD_HR` env var → null + WARNING
- HR zone model: LTHR-based, Friel running boundaries compressed to 5 buckets (not max-HR-based, not Garmin's boundaries)
- Aerobic decoupling: speed/HR ratio, not pace/HR
- TRIMP: Banister formula with gender-selectable coefficients
- GAP: Strava-style polynomial approximation

## Relationship to training-insights

The Training Insights Rails application (separate repo) is one consumer of this pipeline. It expects the payload shape documented above and is configured as one entry in `WEBHOOK_DESTINATIONS`, authenticating with that destination's own secret. Additional destinations (e.g. a logging sink) can be added without affecting it.

Changes to the payload schema that affect Training Insights require coordinated updates in both repos. The `schema_version` field is the mechanism for managing this evolution.

The Training Insights implementation uses a private application layer on top of this framework — a custom processor chain that configures field filtering and metric computation specific to that deployment. That application layer is not part of this repository.
