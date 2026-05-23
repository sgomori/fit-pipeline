# Contributing

## Development setup

```bash
# Install dependencies (user install — no venv required)
pip install --user -e ".[dev]"

# Verify
make check
```

If `pip` is not available: `python3 -m ensurepip --user` or `curl https://bootstrap.pypa.io/get-pip.py | python3 - --user`.

## Running the pipeline

```bash
# Single file, dry run (prints payload to stdout, no delivery)
DRY_RUN=true python pipeline.py tests/fixtures/sample_run.fit

# With full analytics config
THRESHOLD_HR=162 RESTING_HR=48 MAX_HR=185 DRY_RUN=true \
  python pipeline.py tests/fixtures/sample_run.fit

# Directory batch run
DRY_RUN=true python pipeline.py /path/to/fit/files/
```

## Running the server

```bash
SERVER_SECRET=dev_secret DRY_RUN=true python server.py
```

## Tests

```bash
make test          # run pytest
make lint          # ruff check
make typecheck     # mypy
make check         # lint + typecheck + test (full gate)
```

Test files mirror source structure: `tests/test_parser.py` tests `fit_pipeline/parser.py`.

Parser and analytics tests require `tests/fixtures/sample_run.fit`. This file is an anonymized Garmin running activity included in the repository.

## Adding a processor

See `docs/middleware.md` for the full processor API. Use the `/new-processor` Claude skill to scaffold boilerplate.

Quick steps:
1. Create `fit_pipeline/middleware/my_processor.py` subclassing `Processor`
2. Add the class to `PROCESSOR_CHAIN` in `processors.py`
3. Write tests in `tests/test_my_processor.py`

See `examples/custom_processor.py` for a documented example.

## Branching and commits

All work on feature branches. Merge to main via squash — one clean commit per branch.

Commit style: imperative mood, ~50 characters, no trailers.

```
Add aerobic decoupling to StandardAnalyticsProcessor
Fix stream sampling off-by-one on short activities
```

## Architecture constraints

- All `garmin_fit_sdk` interaction stays in `fit_pipeline/parser.py`
- All HTTP interaction stays in `fit_pipeline/delivery.py`
- Processors do not read `os.environ` — use `self.config`
- `process()` must return a dict, never `None`
- The pipeline fails loudly — no silent exception swallowing

See `CLAUDE.md` for the full list of conventions.
