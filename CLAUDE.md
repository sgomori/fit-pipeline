# CLAUDE.md

Project-specific conventions for working in the fit-pipeline codebase.

Read this file at the start of each session. Also read README.md, docs/TECHNICAL_SPEC.md, and docs/V1_SCOPE.md for full context.

## Project overview

fit-pipeline is a Python framework for parsing Garmin FIT files and delivering structured JSON to a webhook endpoint. It is a portfolio project and the data ingestion layer for the training-insights Rails application.

The framework has three layers: parsing (via Garmin's official Python SDK), middleware (configurable processor chain), and delivery (webhook or file). Keep these concerns separated.

## Core principles to preserve

- **Fail loudly.** Errors exit with non-zero codes and clear log messages. No silent failures, no swallowed exceptions.
- **Middleware is opt-in.** The core pipeline works without StandardAnalyticsProcessor. Never make analytics mandatory.
- **The framework is source-agnostic about its middleware.** The core never imports from `middleware/standard_analytics.py` directly — processors are registered by the user.
- **Batch processing is restart-safe.** Completed files must be moved before attempting the next file, not after the batch finishes.
- **Schema version is always present.** Every payload includes `"schema_version"` (currently `"1.1"`, defined as `SCHEMA_VERSION` in `fit_pipeline/core.py`). Do not omit it.

## Branching and commit workflow

All work happens on feature branches. Main branch history is maintained via squash and merge — each merged branch becomes one clean commit on main.

**Branch commits:** Commit whenever a logical unit of work is complete. Messages should be clear enough to review but don't need to be perfect. These are ephemeral.

**Squash commits:** When instructed to squash a branch, review the branch commits, synthesize what collectively changed, and write one clean commit message following the style guidelines below. Execute via `git rebase -i` or `git reset` + recommit.

## Commit conventions

Same style as training-insights: imperative mood, approximately 50 characters, no AI co-author trailers, no "Generated with Claude Code" markers. Match what an experienced human developer would write.

Good examples:
```
Add aerobic decoupling calculation to StandardAnalyticsProcessor
Fix stream sampling off-by-one on short activities
Add batch processing with completed-file management
Extract delivery logic into dedicated module
```

## Code style

- Python 3.10+
- Follow PEP 8
- Type hints on all public functions and class methods
- Docstrings on all public classes and methods (Google style)
- 4-space indentation
- Black for formatting (if available; otherwise standard PEP 8)
- `snake_case` for functions, variables, and modules
- `PascalCase` for classes

## Project structure conventions

- All SDK interaction is isolated in `parser.py`. Nothing else imports from `garmin_fit_sdk` directly.
- All HTTP interaction is isolated in `delivery.py`. Nothing else makes HTTP requests.
- Configuration is loaded once in `config.py` and passed to components. Components do not read environment variables directly.
- Middleware processors live in `fit_pipeline/middleware/`. Each processor is its own file.

## Middleware conventions

- Every processor must subclass `Processor` from `fit_pipeline/processor.py`
- `process()` must return a dict. Never return None.
- Processors may raise exceptions — the pipeline catches them and exits with code 1.
- Processors should not have side effects beyond transforming the data dict.
- Processors should log at DEBUG level what they're doing, INFO level for significant decisions.

## Testing conventions

- pytest for all tests
- Test files mirror source structure: `tests/test_parser.py` tests `fit_pipeline/parser.py`
- Use real FIT file fixtures from `tests/fixtures/` for parser and analytics tests
- Mock the HTTP layer in delivery tests — never make real HTTP calls in tests
- Each analytics metric gets its own test with a known input and expected output
- Test the failure cases explicitly — malformed files, missing config, non-200 responses

## What not to add

- No AI co-author trailers in commits
- No "Generated with Claude Code" comments in code
- No direct database connections — delivery is always webhook or file
- No auto-discovery of processors — registration is explicit
- No retry loops beyond the single configured retry
- No handling of non-running activity types in v1 (cycling, swimming) — skip or log a warning

## HTTP endpoint conventions

The FastAPI server in `fit_pipeline/server.py` and `server.py` shares configuration and pipeline logic with the CLI. Conventions:

- The server does not define pipeline logic — it calls the same batch/pipeline functions the CLI uses
- Authentication is checked before any file system access
- All responses are JSON with a consistent shape (status, processed count, failed count, per-file results)
- Server errors return structured JSON, never raw exception tracebacks
- The server is tested with FastAPI's TestClient — no real HTTP calls in tests

## Configuration access pattern

Components receive configuration as constructor arguments or function parameters. They do not read `os.environ` directly. `config.py` is the single place that reads environment variables.

```python
# Correct
class WebhookDelivery:
    def __init__(self, url: str, secret: str):
        self.url = url
        self.secret = secret

# Incorrect
class WebhookDelivery:
    def deliver(self, payload):
        url = os.environ.get('WEBHOOK_URL')  # don't do this
```

## Logging conventions

- Use Python's standard `logging` module
- Logger names match module names: `logger = logging.getLogger(__name__)`
- DEBUG: detailed processing steps, field values during development
- INFO: file processed, payload delivered, batch progress
- WARNING: non-fatal issues (file move failure, null metric due to missing stream)
- ERROR: fatal issues before exit (malformed file, delivery failure)
- Never print() — always log

## Payload field naming

Field names in the payload use `snake_case`. Units are explicit in field names where ambiguity exists (`distance_meters`, `duration_seconds`, `pace_per_km`). Do not use abbreviated names (`dist`, `dur`, `hr`). The full field names match the schema documented in `docs/payload_schema.md`.

## Open decisions

These are unresolved and should be flagged rather than decided silently:

- Handling of non-running activity types
- Stream data precision (integer vs float for pace values)
- Whether `completed/` directory path is configurable

When you encounter one of these, surface the decision rather than choosing silently.

**Resolved decisions** (do not reopen):
- Processor registration: Python config module (`processors.py` at project root)
- LTHR fallback: skip TSS and HR zones gracefully (null), log WARNING
- HR zones: LTHR-based 5-zone Friel model (not max-HR-based)
- Aerobic decoupling: uses speed/HR ratio (not pace/HR) — see TECHNICAL_SPEC.md amendments

## Claude agent and model usage

### Model selection
- **Planning and architecture** — switch to Opus with `/fast` before design discussions. This project has significant domain complexity (sports science formulas, framework design) that benefits from highest reasoning quality.
- **Implementation** — Sonnet (default). Adequate for translating a well-specified plan into clean Python; faster iteration.
- **Code review** — use `/code-review` after any meaningful implementation chunk. Switch to `/fast` first for highest-quality review.

### Code review cadence
Run `/code-review` after completing each build phase (parser, middleware, delivery, server). Do not skip this step before committing phase work. A Stop hook will remind you when staged changes are present.

### Subagent types in this project
- `Explore` — for locating existing implementations, grep for symbols, find files. Use before implementing anything to check if it already exists.
- `Plan` — for designing the analytics layer or any significant new component before writing code.
- `claude` (general) — for multi-step tasks that don't fit the above.

### Makefile shortcuts
```bash
make test       # run pytest
make lint       # ruff check
make format     # black + ruff fix
make typecheck  # mypy
make check      # lint + typecheck + test
make review     # prints /code-review reminder
```
