# Payload Schema

Every payload delivered by fit-pipeline is a JSON object with the following top-level structure.

## Envelope Fields

These fields are always present.

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | Always `"1.0"` |
| `source` | string | Always `"garmin_fit"` |
| `file` | string | Source filename (e.g. `"my_run.fit"`) |
| `processed_at` | string (ISO 8601) | UTC timestamp when the pipeline ran |
| `activity` | object | Activity summary — see below |

## activity

Core metrics extracted from the FIT session message.

| Field | Type | Unit | Nullable |
|---|---|---|---|
| `started_at` | string (ISO 8601) | — | no |
| `type` | string | — | no — always `"running"` in v1 |
| `distance_meters` | float | m | yes |
| `duration_seconds` | float | s | yes |
| `moving_time_seconds` | float | s | yes |
| `elevation_gain_meters` | float | m | yes |
| `elevation_loss_meters` | float | m | yes |
| `average_heart_rate` | int | bpm | yes |
| `max_heart_rate` | int | bpm | yes |
| `average_cadence` | int | spm (strides/min) | yes |
| `max_cadence` | int | spm | yes |
| `average_power` | int | W | yes |
| `max_power` | int | W | yes |
| `normalized_power` | int | W | yes |
| `total_calories` | int | kcal | yes |
| `average_pace_per_km` | float | s/km | yes |
| `training_stress_score` | float | — | yes — device TSS if present |
| `temperature_celsius` | int | °C | yes |

Fields with `null` values are omitted from the payload entirely (not included as explicit nulls).

`average_cadence` is sourced from `avg_running_cadence` if present, falling back to `avg_cadence`. Both represent strides per minute (one foot strike = one stride).

## computed_metrics

Present only when `StandardAnalyticsProcessor` is in the processor chain. All fields are nullable — a missing required stream results in `null` rather than an error.

| Field | Type | Unit | Nullable |
|---|---|---|---|
| `aerobic_decoupling_pct` | float | % | yes — requires pace + HR streams |
| `efficiency_factor` | float | m/min/bpm | yes — requires pace + HR streams |
| `cardiac_drift_bpm` | int | bpm | yes — requires HR stream ≥ 8 records |
| `tss_score` | float | TSS | yes (hrTSS) — requires LTHR |
| `rtss_score` | float | TSS | yes (rTSS via NGP) — requires THRESHOLD_PACE + speed/altitude streams |
| `pace_cv` | float | — | yes — coefficient of variation of pace; requires pace stream |
| `hr_zone_distribution` | object | — | yes — requires HR stream + LTHR |
| `pace_zone_distribution` | object | — | yes — requires pace zone config |
| `trimp` | float | — | yes — requires RESTING_HR config |
| `avg_grade_adjusted_pace_per_km` | float | s/km | yes — requires altitude + pace streams |
| `grade_adjusted_efficiency_factor` | float | m/min/bpm | yes — requires altitude + pace streams |

### hr_zone_distribution

```json
{
  "zone_1": 100.0,
  "zone_2": 0.0,
  "zone_3": 0.0,
  "zone_4": 0.0,
  "zone_5": 0.0
}
```

Values are percentages summing to 100. Zone boundaries use the Friel LTHR model by default (see `docs/middleware.md`).

### pace_zone_distribution

```json
{
  "easy": 41.8,
  "moderate": 56.0,
  "threshold": 2.0,
  "hard": 0.3
}
```

Values are percentages summing to 100. Boundaries are configured via `PACE_ZONE_EASY`, `PACE_ZONE_MODERATE`, `PACE_ZONE_THRESHOLD` (s/km).

## laps

Present when the FIT file contains lap messages. Array of lap summary objects.

| Field | Type | Unit | Nullable |
|---|---|---|---|
| `started_at` | string (ISO 8601) | — | yes |
| `distance_meters` | float | m | yes |
| `duration_seconds` | float | s | yes |
| `average_heart_rate` | int | bpm | yes |
| `max_heart_rate` | int | bpm | yes |
| `average_cadence` | int | spm | yes |
| `average_pace_per_km` | float | s/km | yes |

## streams

Present only when `INCLUDE_STREAMS=true` (default). Dict of stream name → array of values sampled at `STREAM_SAMPLE_RATE` seconds.

Common streams (availability depends on device and sensors):

| Stream | Type | Unit |
|---|---|---|
| `heart_rate` | int[] | bpm |
| `cadence` | int[] | spm |
| `enhanced_speed` | float[] | m/s |
| `enhanced_altitude` | float[] | m |
| `power` | int[] | W |
| `distance` | float[] | m |
| `temperature` | int[] | °C |
| `vertical_oscillation` | float[] | mm |
| `stance_time` | float[] | ms |
| `position_lat` | int[] | semicircles | _(excluded when `EXCLUDE_GPS=true`)_ |
| `position_long` | int[] | semicircles | _(excluded when `EXCLUDE_GPS=true`)_ |

GPS streams (`position_lat`, `position_long`) are excluded by default (`EXCLUDE_GPS=true`). Set `EXCLUDE_GPS=false` to include them.

Device info fields are excluded by default (`EXCLUDE_DEVICE_INFO=true`).

## Example Payload

```json
{
  "schema_version": "1.0",
  "source": "garmin_fit",
  "file": "morning_run.fit",
  "processed_at": "2024-01-15T12:34:56.789000+00:00",
  "activity": {
    "started_at": "2024-01-15T07:00:00+00:00",
    "type": "running",
    "distance_meters": 3156.5,
    "duration_seconds": 1127.2,
    "moving_time_seconds": 1127.2,
    "elevation_gain_meters": 15.0,
    "elevation_loss_meters": 28.0,
    "average_heart_rate": 130,
    "max_heart_rate": 141,
    "average_cadence": 88,
    "average_power": 304,
    "normalized_power": 306,
    "total_calories": 236,
    "average_pace_per_km": 357.1
  },
  "computed_metrics": {
    "aerobic_decoupling_pct": 7.88,
    "efficiency_factor": 1.288,
    "cardiac_drift_bpm": 21,
    "tss_score": 19.0,
    "rtss_score": 22.9,
    "pace_cv": 0.0917,
    "hr_zone_distribution": {
      "zone_1": 100.0,
      "zone_2": 0.0,
      "zone_3": 0.0,
      "zone_4": 0.0,
      "zone_5": 0.0
    },
    "pace_zone_distribution": {
      "easy": 41.8,
      "moderate": 56.0,
      "threshold": 2.0,
      "hard": 0.3
    },
    "trimp": 22.7,
    "avg_grade_adjusted_pace_per_km": 351.1,
    "grade_adjusted_efficiency_factor": 1.3147
  },
  "laps": [
    {"started_at": "2024-01-15T07:00:00+00:00", "distance_meters": 1000.0, "duration_seconds": 360.0, "average_heart_rate": 119, "average_cadence": 86, "average_pace_per_km": 360.0},
    {"started_at": "2024-01-15T07:06:00+00:00", "distance_meters": 1000.0, "duration_seconds": 357.4, "average_heart_rate": 132, "average_cadence": 88, "average_pace_per_km": 357.4}
  ]
}
```
