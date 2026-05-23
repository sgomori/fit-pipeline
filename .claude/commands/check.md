Run the full quality gate: lint, type check, and test suite. Report any failures clearly.

```bash
cd /home/sgomori/projects/fit-pipeline && make check
```

If `make check` is unavailable, run each step individually:

```bash
cd /home/sgomori/projects/fit-pipeline && \
  python3 -m ruff check fit_pipeline/ tests/ && \
  python3 -m mypy fit_pipeline/ --ignore-missing-imports && \
  python3 -m pytest tests/ -v
```

After reviewing the output:
- Fix any type errors or lint violations before committing
- If tests fail, investigate root cause — do not suppress or skip
- If all pass, confirm the count: "X/X tests passed, no lint or type errors"
