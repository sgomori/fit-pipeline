# fit-pipeline — v1 Scope

This document defines what's in v1, what's deferred, and the explicit non-goals.

## The three things v1 must deliver

1. **A working FIT parser that extracts activity and stream data via the Garmin SDK.** Clean extraction of session summary fields and time-series streams. Configurable field filtering. Configurable stream sampling.

2. **A middleware interface that works.** The base Processor class, a StandardAnalyticsProcessor that computes all seven target metrics, a FieldFilterProcessor as a simple demonstration, and an example implementation that shows how to build custom middleware.

3. **Reliable delivery with batch support.** Webhook delivery with retry, file output as alternative, dry-run mode, batch directory processing with completed-file management and restart safety.

If v1 ships with these three things working well and tested, it's a success.

## In scope for v1

### Core pipeline

- FIT file parsing via Garmin's official Python SDK
- Extraction of session, record, lap, and event messages
- Field filtering via environment variable configuration
- GPS coordinate exclusion (default on)
- Device info exclusion (default on)
- Stream sampling at configurable rate (default 3 seconds)
- Schema version field in every payload (`"schema_version": "1.1"`)
- Single file and directory (batch) invocation via CLI

### Middleware

- Base `Processor` class with documented interface
- `StandardAnalyticsProcessor` computing:
  - aerobic_decoupling_pct
  - efficiency_factor
  - cardiac_drift_bpm
  - tss_score
  - pace_cv
  - hr_zone_distribution
  - pace_zone_distribution
- `FieldFilterProcessor` as a simple demonstration processor
- `examples/custom_processor.py` as a documented starting point
- `docs/middleware.md` with full API reference

### Delivery

- Webhook POST with Bearer token authentication
- Single retry on failure
- File output as alternative to webhook
- Dry-run mode (stdout output, no delivery)
- Configurable log file

### Batch processing

- Chronological file ordering
- Sequential processing with await-200-before-next behavior
- Completed file movement to `completed/` subdirectory
- Failed file retention in source directory
- Batch halt on first failure
- Restart safety (completed files are not reprocessed)

### Error handling

- Loud failure on malformed FIT file (exit code 1, no POST)
- Loud failure on missing required configuration (exit code 1)
- Loud failure on webhook non-200 after retry (exit code 1)
- Loud failure on middleware exception (exit code 1)
- Warning (non-fatal) on completed file move failure

### HTTP endpoint

- FastAPI app exposing `POST /process` for remote triggering
- Bearer token authentication via `SERVER_SECRET`
- Accepts file path or directory path in request body
- Returns structured JSON response with processing results
- Shares configuration and pipeline logic with CLI invocation
- `test_server.py` covering auth, valid requests, and error responses
- `docs/server.md` documenting the endpoint for self-hosters

### Testing

- pytest test suite
- Anonymized real FIT file fixture(s) with known expected outputs
- Tests for parser, StandardAnalyticsProcessor, delivery, and batch logic
- HTTP layer mocked in delivery tests

### Documentation

- README with quick start, configuration reference, and middleware overview
- TECHNICAL_SPEC.md (this document's pair)
- `docs/middleware.md` — middleware API reference
- `docs/payload_schema.md` — full payload schema with field descriptions and units
- `.env.example` — configuration template
- CONTRIBUTING.md — contribution guidelines and scope boundaries
- CLAUDE.md — project conventions for Claude Code

## Explicitly out of scope for v1

- Plugin discovery mechanism (auto-discovery of processors from installed packages)
- Non-running activity types (cycling, swimming) — running is the only supported type in v1; other types are parsed with reduced field sets or skipped
- Multi-format output (currently only JSON webhook payload)
- Garmin health data (sleep, HRV, weight) — this pipeline handles FIT activity files only; health data arrives via a separate path
- Direct database writes — delivery is always via webhook or file, never direct DB connection
- Web interface or API server of any kind — CLI only
- Authentication schemes beyond Bearer token
- Configurable retry count or retry delay — single retry with 2-second delay is fixed in v1
- Streaming/real-time processing — file-based batch only

## Non-goals

- **Not a general FIT file viewer.** The pipeline extracts what's analytically useful for training analysis. It does not attempt to expose every FIT message type or field.
- **Not a replacement for the Garmin SDK.** This project wraps the SDK. It does not re-implement FIT parsing.
- **Not a training platform.** The pipeline produces data. What downstream consumers do with that data is their concern.
- **Not a managed service.** The pipeline is a script. It runs when invoked and exits. No persistent process, no API server, no queue consumer.

## Build approach

Built with Claude Code assistance. The build should proceed before the Training Insights Rails application where possible — the webhook payload schema produced by this pipeline defines the data model the Rails app receives. Getting this right first prevents rework in the Rails project.

The FIT fixture file for testing should be prepared early — it's needed for test-driven development of the analytics layer.

## Repository visibility

Public, under the MIT license (see `LICENSE` at the repository root). The
Training Insights Rails application that consumes this pipeline is public under
the same terms at https://github.com/sgomori/training-insights.

The n8n workflow repository (training-insights-n8n) remains private while it is
still in development.

## When v1 is "done"

V1 is done when:

- A real FIT file from a Garmin running activity is successfully parsed and delivered to a webhook endpoint
- StandardAnalyticsProcessor produces correct metric values verified against known test fixtures
- Batch processing correctly handles a directory of multiple FIT files, moves completed files, and halts on failure
- Dry-run mode works and produces readable output
- The test suite passes
- All documentation files are accurate and complete
- The `.env.example` file covers every configuration variable

No specific date. The bar is a working, tested, documented pipeline that can process real data.
