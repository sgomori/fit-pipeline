Regenerate the expected analytics fixture from the current StandardAnalyticsProcessor output.

Run this after intentionally changing a formula in standard_analytics.py to accept the new
values as correct. Always run /verify-analytics first to confirm the changes are expected,
then run this skill to update the fixture.

```bash
cd /home/sgomori/projects/fit-pipeline && python3 - <<'EOF'
import json
from fit_pipeline.config import Config
from fit_pipeline.parser import parse_fit_file
from fit_pipeline.middleware.standard_analytics import StandardAnalyticsProcessor

cfg = Config(
    dry_run=True, include_streams=True, stream_sample_rate=1,
    exclude_gps=True, exclude_device_info=True,
    threshold_hr=None, resting_hr=48, max_hr=185,
    trimp_gender="male",
    pace_zone_easy=360, pace_zone_moderate=330, pace_zone_threshold=300,
    threshold_pace=300,
)
data = parse_fit_file("tests/fixtures/sample_run.fit", cfg)
result = StandardAnalyticsProcessor(cfg).process(data)
actual = result.get("computed_metrics", {})

with open("tests/fixtures/sample_run_expected.json") as f:
    fixture = json.load(f)
old = fixture.get("computed_metrics", {})

print("=== CHANGES ===")
changed = False
for k in sorted(set(old) | set(actual)):
    old_val = old.get(k)
    new_val = actual.get(k)
    if old_val != new_val:
        print(f"  {k}: {old_val}  →  {new_val}")
        changed = True
if not changed:
    print("  (no changes — fixture is already up to date)")

fixture["computed_metrics"] = actual
with open("tests/fixtures/sample_run_expected.json", "w") as f:
    json.dump(fixture, f, indent=2)
    f.write("\n")

print()
print("Updated tests/fixtures/sample_run_expected.json")
EOF
```
