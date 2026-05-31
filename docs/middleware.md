# Middleware Reference

fit-pipeline uses a processor chain pattern for transforming parsed FIT data before delivery. Each processor receives the full data dict, adds or transforms fields, and returns the updated dict.

## Architecture

```
parse_fit_file()  →  [Processor, Processor, ...]  →  _build_payload()  →  delivery
```

The chain is defined in `processors.py` at the project root:

```python
from fit_pipeline.middleware.field_filter import FieldFilterProcessor
from fit_pipeline.middleware.standard_analytics import StandardAnalyticsProcessor

PROCESSOR_CHAIN = [StandardAnalyticsProcessor, FieldFilterProcessor]
```

Processors run in order. Each processor receives the output of the previous one. The order matters — `StandardAnalyticsProcessor` must run before `FieldFilterProcessor` if you want analytics computed before any field stripping.

## Built-in Processors

### StandardAnalyticsProcessor

`fit_pipeline/middleware/standard_analytics.py`

Computes 9 metrics from the parsed streams and activity summary. All metrics are written to `data["computed_metrics"]`. A missing required stream results in `null` for that metric — it never raises an exception.

See [Analytics Metrics](#analytics-metrics) below for full formula documentation.

**Configuration env vars used:**

| Variable | Used by |
|---|---|
| `THRESHOLD_HR` | TSS (hrTSS), HR zone distribution |
| `MAX_HR` | TRIMP (physiological ceiling; overrides session max) |
| `RESTING_HR` | TRIMP |
| `TRIMP_GENDER` | TRIMP coefficient selection (`male`/`female`) |
| `PACE_ZONE_EASY` | Pace zone distribution (upper boundary, s/km) |
| `PACE_ZONE_MODERATE` | Pace zone distribution (upper boundary, s/km) |
| `PACE_ZONE_THRESHOLD` | Pace zone distribution (upper boundary, s/km) |
| `HR_ZONE_1` – `HR_ZONE_5` | HR zone overrides (fixed BPM upper boundaries) |

**LTHR resolution order (per activity):**

1. `zones_target_mesgs.threshold_heart_rate` from the FIT file (Garmin auto-detects from threshold runs)
2. `THRESHOLD_HR` environment variable
3. Neither present → `tss_score` and `hr_zone_distribution` return `null`; WARNING logged

### FieldFilterProcessor

`fit_pipeline/middleware/field_filter.py`

Removes fields from `data["activity"]` based on the `EXCLUDE_FIELDS` config variable. Useful for stripping proprietary or unwanted fields before delivery.

**Configuration env vars used:**

| Variable | Description |
|---|---|
| `EXCLUDE_FIELDS` | Comma-separated list of field names to remove from `activity` |

## Writing a Custom Processor

Use the `/new-processor` skill to scaffold the boilerplate. Manual steps:

1. Create `fit_pipeline/middleware/my_processor.py` subclassing `Processor`
2. Implement `process(self, data: dict) -> dict` — must return a dict, never `None`
3. Add the class to `PROCESSOR_CHAIN` in `processors.py`

```python
from fit_pipeline.config import Config
from fit_pipeline.processor import Processor

class MyProcessor(Processor):
    """One-line description."""

    def process(self, data: dict) -> dict:
        activity = data.get("activity", {})
        metrics = data.get("computed_metrics", {})

        # read from streams, activity, or prior computed_metrics
        # write new fields into metrics or activity

        data["computed_metrics"] = metrics
        return data
```

**Rules:**
- Return the full `data` dict, not just the fields you touched
- Do not read `os.environ` directly — use `self.config`
- Log at `DEBUG` level for processing steps, `WARNING` for graceful null returns
- Processors must not make HTTP requests or write to disk

## Analytics Metrics

All metrics are computed by `StandardAnalyticsProcessor` and written to `data["computed_metrics"]`.

### aerobic_decoupling_pct

Measures cardiovascular drift across the activity. Uses speed/HR ratio (TrainingPeaks Pa:HR convention).

- Split records at the elapsed-time midpoint
- `eff = avg_speed_m_per_min / avg_hr` for each half
- `decoupling = (eff_h1 - eff_h2) / eff_h1 × 100`
- Positive = HR drifted up relative to speed; < 5% indicates aerobic efficiency
- Requires: pace stream + heart_rate stream

### efficiency_factor

Speed-per-heartbeat efficiency ratio. Higher = more efficient aerobic system.

- `EF = avg_speed_m_per_min / avg_heart_rate`
- Expected range: ~1.2–1.8 for trained runners
- Requires: pace stream + heart_rate stream

### cardiac_drift_bpm

HR increase from first to last quarter of the activity at fixed effort.

- Q1 avg HR = mean of first 25% of heart_rate records
- Q4 avg HR = mean of last 25% of heart_rate records
- `cardiac_drift_bpm = Q4_avg - Q1_avg`
- Requires: heart_rate stream with ≥ 8 records
- Note: pace is not controlled — elevation and pacing changes affect the value

### tss_score (hrTSS)

Heart-rate-based Training Stress Score. Quantifies training load relative to threshold intensity.

- `IF = avg_heart_rate / LTHR` (Intensity Factor)
- `hrTSS = (duration_seconds × IF²) / 3600 × 100`
- Null if LTHR is unavailable
- Requires: LTHR (see resolution order above)

### pace_cv

Coefficient of variation of pace — measures pacing consistency. (Not the Coggan Variability Index.)

- `pace_cv = std(pace_s_per_km) / mean(pace_s_per_km)`
- Lower = more consistent pacing
- Requires: pace stream

### hr_zone_distribution

Percentage of time spent in each of 5 HR zones using the Friel LTHR-based model.

**Default zone boundaries (% of LTHR):**

| Zone | Upper Boundary | Description |
|------|----------------|-------------|
| 1 | < 85% | Active Recovery |
| 2 | 85–92% | Aerobic Base |
| 3 | 93–99% | Tempo |
| 4 | 100–105% | Threshold |
| 5 | > 105% | VO2max / Neuromuscular |

Override with fixed BPM via `HR_ZONE_1` through `HR_ZONE_5` env vars (upper boundary of each zone).

- Null if LTHR is unavailable
- Requires: heart_rate stream + LTHR

### pace_zone_distribution

Percentage of time spent in each of 4 pace zones.

| Zone | Condition |
|------|-----------|
| easy | pace > `PACE_ZONE_EASY` (s/km) |
| moderate | `PACE_ZONE_MODERATE` < pace ≤ `PACE_ZONE_EASY` |
| threshold | `PACE_ZONE_THRESHOLD` < pace ≤ `PACE_ZONE_MODERATE` |
| hard | pace ≤ `PACE_ZONE_THRESHOLD` |

- Null if none of the pace zone env vars are configured
- Requires: pace stream

### trimp

Banister Training Impulse — load metric based on HR reserve.

- `hrr = (avg_hr - resting_hr) / (max_hr - resting_hr)`
- Male coefficients (default): `TRIMP = duration_min × hrr × 0.64 × e^(1.92 × hrr)`
- Female coefficients (`TRIMP_GENDER=female`): `TRIMP = duration_min × hrr × 0.86 × e^(1.67 × hrr)`
- `max_hr` resolution: `MAX_HR` config → session `max_heart_rate` field
- Null if `RESTING_HR` is not configured
- Requires: RESTING_HR config + max HR source

### avg_grade_adjusted_pace_per_km / grade_adjusted_efficiency_factor

Grade-Adjusted Pace normalizes pace for elevation to compare flat-equivalent effort.

Per-record adjustment:
- `grade_pct = (alt_diff_m / dist_diff_m) × 100`
- Uphill factor: `1 + 0.033 × grade_pct`
- Downhill factor: `1 - 0.018 × |grade_pct|` (capped at −15%)
- `gap_record = actual_pace_s_per_km / adjustment_factor`

Outputs:
- `avg_grade_adjusted_pace_per_km`: mean of all per-record GAP values
- `grade_adjusted_efficiency_factor`: `avg_speed_from_gap / avg_hr`

- Polynomial is a Strava-style approximation; higher-fidelity models are possible
- Requires: altitude stream (`enhanced_altitude` or `altitude`) + pace stream
