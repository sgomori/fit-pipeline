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

Computes 11 metrics from the parsed streams and activity summary. All metrics are written to `data["computed_metrics"]`. A missing required stream results in `null` for that metric — it never raises an exception.

See [Analytics Metrics](#analytics-metrics) below for full formula documentation.

**Configuration env vars used:**

| Variable | Used by |
|---|---|
| `THRESHOLD_HR` | TSS (hrTSS), HR zone distribution |
| `THRESHOLD_PACE` | rTSS (functional threshold pace, s/km) |
| `MAX_HR` | TRIMP (physiological ceiling; overrides the FIT profile max) |
| `RESTING_HR` | TRIMP |
| `TRIMP_GENDER` | TRIMP coefficient selection (`male`/`female`) |
| `PACE_ZONE_EASY` | Pace zone distribution (upper boundary, s/km) |
| `PACE_ZONE_MODERATE` | Pace zone distribution (upper boundary, s/km) |
| `PACE_ZONE_THRESHOLD` | Pace zone distribution (upper boundary, s/km) |
| `HR_ZONE_1` – `HR_ZONE_4` | HR zone overrides (fixed BPM upper boundaries; all four required) |
| `STREAM_SAMPLE_RATE` | rTSS (converts the 30-second smoothing window into samples) |

**LTHR resolution order (per activity):**

1. `zones_target_mesgs.threshold_heart_rate` from the FIT file (Garmin auto-detects from threshold runs)
2. `THRESHOLD_HR` environment variable
3. Neither usable → `tss_score` and `hr_zone_distribution` return `null`; WARNING logged

A FIT value outside 80–220 BPM is treated as absent and skipped at step 1, with an INFO log naming the value. A watch that has never auto-detected a threshold reports `0` rather than omitting the field, and presence alone therefore does not mean a usable reading — taken literally, `0` is a divide-by-zero in hrTSS and a small non-zero value inflates `tss_score` by orders of magnitude while reporting every sample as zone 5. The same band applies to `zones_target.max_heart_rate` for TRIMP. `THRESHOLD_HR` and `MAX_HR` are validated against it at startup and raise `ConfigError`.

### FieldFilterProcessor

`fit_pipeline/middleware/field_filter.py`

Removes fields from both `data["activity"]` and `data["streams"]` based on the `EXCLUDE_FIELDS` config variable. Useful for stripping proprietary or unwanted fields before delivery.

Note that the parser has already applied `EXCLUDE_FIELDS` to the stream keys by the time this processor runs — this pass catches the activity summary, and re-applies to streams for any keys a preceding processor added.

**Configuration env vars used:**

| Variable | Description |
|---|---|
| `EXCLUDE_FIELDS` | Comma-separated list of field names to remove from `activity` and `streams` |

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
- Requires: pace stream + heart_rate stream, with ≥ 4 paired records

### efficiency_factor

Speed-per-heartbeat efficiency ratio. Higher = more efficient aerobic system.

- `EF = avg_speed_m_per_min / avg_heart_rate`
- Average speed is the arithmetic (time-weighted) mean of the per-record speed samples, not the harmonic mean of pace
- Stopped and near-stopped samples (≤ 0.5 m/s) are excluded
- No grade adjustment — see `grade_adjusted_efficiency_factor`
- Expected range: ~1.2–1.8 for trained runners
- Requires: speed stream + heart_rate stream

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
- Uses *average* HR, so it cannot reward variability — see `rtss_score` for the pace-native score

### rtss_score (rTSS)

Run Training Stress Score from Normalized Graded Pace. Because NGP weights surges, rTSS captures the intensity distribution that hrTSS averages away.

- Grade-adjusted speed is smoothed over a 30-second rolling window (`30 / STREAM_SAMPLE_RATE` samples, minimum 1)
- `NGP_speed` = 4th root of the mean of the rolling values raised to the 4th power
- `threshold_speed = 1000 / THRESHOLD_PACE` (m/s)
- `IF = NGP_speed / threshold_speed`
- `rTSS = (duration_seconds × IF²) / 3600 × 100`
- Null if `THRESHOLD_PACE` is not configured; hrTSS remains the HR-only fallback
- Requires: speed + altitude + distance streams, and `THRESHOLD_PACE`

### pace_cv

Coefficient of variation of pace — measures pacing consistency. (Not the Coggan Variability Index.)

- `pace_cv = std(pace_s_per_km) / mean(pace_s_per_km)` (stopped samples excluded)
- Lower = more consistent pacing
- Requires: pace stream, with ≥ 2 moving records

### hr_zone_distribution

Percentage of time spent in each of 5 HR zones using the Friel LTHR-based model.

**Default zone boundaries (% of LTHR):**

| Zone | Upper Boundary | Description |
|------|----------------|-------------|
| 1 | < 85% | Active Recovery |
| 2 | 85–89% | Aerobic Base |
| 3 | 90–99% | Tempo |
| 4 | 100–106% | Threshold |
| 5 | > 106% | VO2max / Neuromuscular |

Override with fixed BPM via the `HR_ZONE_1` through `HR_ZONE_4` env vars (upper boundary of each zone). All four must be set — a partial override is ignored and every boundary falls back to the LTHR percentages, so the zones always come from one consistent source. Zone 5 is unbounded above, so `HR_ZONE_5` is loaded but never read.

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
- `max_hr` resolution: `MAX_HR` config → FIT `zones_target.max_heart_rate` (the athlete's profile maximum). The session peak is **not** used — Banister TRIMP requires the physiological maximum.
- Null if `RESTING_HR` is not configured, or if no max HR is available
- Requires: RESTING_HR config + max HR source

### avg_grade_adjusted_pace_per_km / grade_adjusted_efficiency_factor

Grade-Adjusted Pace normalizes pace for elevation to compare flat-equivalent effort. The adjustment is applied to *speed*, per record, against the previous sample:

- `grade_pct = (alt_diff_m / dist_diff_m) × 100`
- Uphill (`grade_pct ≥ 0`) factor: `1 + 0.033 × grade_pct`
- Downhill factor: `1 - 0.018 × |grade_pct|`, with the grade first clamped to a −15% floor
- The factor is floored at 0.5 as a sanity guard on extreme uphill grades
- `gap_speed = speed_m_per_s × factor`
- Samples with no distance gain (`dist_diff ≤ 0`), and the first sample, are carried through unadjusted

Outputs:
- `avg_grade_adjusted_pace_per_km`: `1000 / mean(gap_speed)` — the pace equivalent of the mean grade-adjusted speed, not the mean of per-record GAP paces
- `grade_adjusted_efficiency_factor`: `mean(gap_speed) × 60 / avg_hr` (m/min per BPM, matching `efficiency_factor`); null when no HR is available

- Polynomial is a Strava-style approximation; higher-fidelity models are possible
- Requires: speed + altitude (`enhanced_altitude` or `altitude`) + distance streams, ≥ 2 records each
