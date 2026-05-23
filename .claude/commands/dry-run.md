Run the full pipeline in dry-run mode against the anonymized sample FIT fixture.

```bash
cd /home/sgomori/projects/fit-pipeline && \
  THRESHOLD_HR=167 RESTING_HR=48 MAX_HR=185 \
  TRIMP_GENDER=male \
  PACE_ZONE_EASY=360 PACE_ZONE_MODERATE=330 PACE_ZONE_THRESHOLD=300 \
  DRY_RUN=true INCLUDE_STREAMS=false \
  python pipeline.py tests/fixtures/sample_run.fit 2>&1
```

Review the printed payload. Key things to check:
- `schema_version` is present
- `computed_metrics` block is present and non-null
- `activity.type` is `running`
- No WARNING-level log lines about missing streams or LTHR
