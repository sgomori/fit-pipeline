Run the analytics processor against the sample FIT fixture and diff the output against the expected fixture.

```bash
cd /home/sgomori/projects/fit-pipeline && python3 - <<'EOF'
import json, sys
from fit_pipeline.config import Config
from fit_pipeline.parser import parse_fit_file
from fit_pipeline.middleware.standard_analytics import StandardAnalyticsProcessor

cfg = Config(
    dry_run=True, include_streams=True, stream_sample_rate=1,
    exclude_gps=True, exclude_device_info=True,
    threshold_hr=None, resting_hr=48, max_hr=185,
    trimp_gender="male",
    pace_zone_easy=360, pace_zone_moderate=330, pace_zone_threshold=300,
)
data = parse_fit_file("tests/fixtures/sample_run.fit", cfg)
result = StandardAnalyticsProcessor(cfg).process(data)
actual = result.get("computed_metrics", {})

with open("tests/fixtures/sample_run_expected.json") as f:
    expected = json.load(f).get("computed_metrics", {})

print("=== COMPARISON ===")
all_ok = True
for k, exp_val in expected.items():
    act_val = actual.get(k)
    if isinstance(exp_val, dict):
        match = exp_val == act_val
    elif isinstance(exp_val, float):
        match = act_val is not None and abs(act_val - exp_val) < 0.05
    else:
        match = act_val == exp_val
    status = "OK " if match else "FAIL"
    if not match:
        all_ok = False
    print(f"  {status}  {k}: expected={exp_val}, got={act_val}")

print()
print("ALL PASS" if all_ok else "FAILURES DETECTED")
EOF
```
