"""FIT file parsing via the Garmin Python SDK.

All garmin_fit_sdk interaction is isolated here. No other module imports
from garmin_fit_sdk directly.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from garmin_fit_sdk import Decoder, Stream

from fit_pipeline.config import Config
from fit_pipeline.exceptions import ParseError

logger = logging.getLogger(__name__)

# GPS fields excluded by default (EXCLUDE_GPS=true)
_GPS_FIELDS = {"position_lat", "position_long"}

# Device info fields excluded by default (EXCLUDE_DEVICE_INFO=true)
_DEVICE_INFO_FIELDS = {
    "serial_number",
    "manufacturer",
    "garmin_product",
    "hardware_version",
    "software_version",
    "device_index",
}


def parse_fit_file(path: str | Path, config: Config) -> dict[str, Any]:
    """Parse a FIT file and return a structured activity dict.

    Args:
        path: Path to the .fit file.
        config: Loaded pipeline configuration.

    Returns:
        Structured dict with keys: activity, streams, laps, zones_target.
        streams is present only when config.include_streams is True.

    Raises:
        ParseError: If the file cannot be read, is not a valid FIT file,
            or produces SDK decode errors.
    """
    path = Path(path)
    logger.info("Parsing FIT file: %s", path.name)

    if not path.exists():
        raise ParseError(f"File not found: {path}")

    try:
        stream = Stream.from_file(str(path))
        decoder = Decoder(stream)
    except Exception as exc:
        raise ParseError(f"Failed to open FIT file {path.name}: {exc}") from exc

    if not decoder.is_fit():
        raise ParseError(f"Not a valid FIT file: {path.name}")

    messages, errors = decoder.read(
        apply_scale_and_offset=True,
        convert_datetimes_to_dates=True,
        convert_types_to_strings=True,
        expand_sub_fields=True,
        expand_components=True,
        merge_heart_rates=True,
    )

    if errors:
        # Log all errors, then raise on the first one
        for err in errors:
            logger.error("FIT decode error in %s: %s", path.name, err)
        raise ParseError(
            f"FIT decode errors in {path.name}: {errors[0]}"
        )

    logger.debug("Decoded message types: %s", list(messages.keys()))

    # Streams are always extracted so analytics processors have access to them.
    # The final payload includes or excludes them based on config.include_streams,
    # which is handled by core._build_payload — not here.
    result: dict[str, Any] = {
        "activity": _extract_session(messages, path.name, config),
        "laps": _extract_laps(messages),
        "zones_target": _extract_zones_target(messages),
        "streams": _extract_streams(messages, config),
    }

    return result


def _extract_session(
    messages: dict[str, Any], filename: str, config: Config
) -> dict[str, Any]:
    """Extract the activity summary from session messages.

    Args:
        messages: Decoded FIT messages from Decoder.read().
        filename: Source filename (used in logging).
        config: Pipeline config for field filtering.

    Returns:
        Activity summary dict.
    """
    session_mesgs = messages.get("session_mesgs", [])
    if not session_mesgs:
        raise ParseError(f"No session message found in {filename}")

    if len(session_mesgs) > 1:
        logger.warning(
            "%s contains %d session messages; using the first",
            filename,
            len(session_mesgs),
        )

    session = session_mesgs[0]
    logger.debug("Session fields: %s", list(session.keys()))

    activity: dict[str, Any] = {}

    # Timestamps
    start_time = session.get("start_time")
    if isinstance(start_time, datetime):
        activity["started_at"] = start_time.astimezone(timezone.utc).isoformat()
    elif start_time is not None:
        activity["started_at"] = str(start_time)

    # Sport type
    sport = session.get("sport", "")
    activity["type"] = str(sport).lower() if sport else None

    if activity.get("type") not in ("running", "run", None):
        logger.warning(
            "Activity type %r is not 'running'. "
            "Non-running activity types are not supported in v1.",
            activity.get("type"),
        )

    # Core metrics
    activity["distance_meters"] = _round_or_none(session.get("total_distance"), 1)
    activity["duration_seconds"] = _round_or_none(session.get("total_elapsed_time"), 1)
    activity["moving_time_seconds"] = _round_or_none(session.get("total_timer_time"), 1)
    activity["elevation_gain_meters"] = _round_or_none(session.get("total_ascent"), 1)
    activity["elevation_loss_meters"] = _round_or_none(session.get("total_descent"), 1)
    activity["average_heart_rate"] = session.get("avg_heart_rate")
    activity["max_heart_rate"] = session.get("max_heart_rate")
    activity["average_cadence"] = session.get("avg_running_cadence") or session.get("avg_cadence")
    activity["max_cadence"] = session.get("max_running_cadence") or session.get("max_cadence")
    activity["average_power"] = session.get("avg_power")
    activity["max_power"] = session.get("max_power")
    activity["normalized_power"] = session.get("normalized_power")
    activity["training_stress_score"] = _round_or_none(session.get("training_stress_score"), 1)
    activity["total_calories"] = session.get("total_calories")
    activity["temperature_celsius"] = session.get("avg_temperature")

    # Compute average pace from distance + moving time
    dist = activity.get("distance_meters")
    duration = activity.get("moving_time_seconds") or activity.get("duration_seconds")
    if dist and duration and dist > 0:
        activity["average_pace_per_km"] = round(duration / (dist / 1000), 1)
    else:
        activity["average_pace_per_km"] = None

    # Remove None values (keep the key structure clean for downstream)
    activity = {k: v for k, v in activity.items() if v is not None}

    logger.debug("Extracted %d activity fields", len(activity))
    return activity


def _extract_streams(messages: dict[str, Any], config: Config) -> dict[str, list[Any]]:
    """Extract time-series stream data from record messages.

    Applies stream sampling at config.stream_sample_rate (time-based, not
    index-based). GPS fields are excluded when config.exclude_gps is True.

    Args:
        messages: Decoded FIT messages.
        config: Pipeline config.

    Returns:
        Dict of stream name → sampled list of values.
    """
    records = messages.get("record_mesgs", [])
    if not records:
        logger.warning("No record messages found — streams will be empty")
        return {}

    sample_rate = config.stream_sample_rate
    exclude = set(config.exclude_fields)
    if config.exclude_gps:
        exclude |= _GPS_FIELDS

    # Determine stream keys from the first record that has each field
    stream_keys = set()
    for rec in records[:10]:
        stream_keys.update(rec.keys())
    stream_keys -= {"timestamp"}
    stream_keys -= exclude

    streams: dict[str, list[Any]] = {k: [] for k in stream_keys}
    timestamps: list[Any] = []

    for rec in records:
        ts = rec.get("timestamp")
        timestamps.append(ts)

    # Time-based sampling: include record if its elapsed time is a multiple
    # of sample_rate seconds from the first timestamp
    first_ts = _to_epoch(timestamps[0]) if timestamps else None
    sampled_records: list[dict[str, Any]] = []

    if first_ts is None or sample_rate <= 1:
        sampled_records = records
    else:
        last_included_elapsed: float = -sample_rate
        for rec in records:
            ts = rec.get("timestamp")
            elapsed = _elapsed_seconds(first_ts, ts)
            if elapsed is None or elapsed - last_included_elapsed >= sample_rate:
                sampled_records.append(rec)
                last_included_elapsed = elapsed if elapsed is not None else last_included_elapsed

    logger.debug(
        "Stream sampling: %d records → %d samples (rate=%ds)",
        len(records),
        len(sampled_records),
        sample_rate,
    )

    for key in stream_keys:
        streams[key] = [
            rec.get(key) for rec in sampled_records if key in rec
        ]

    return {k: v for k, v in streams.items() if v}


def _extract_laps(messages: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract lap summaries.

    Args:
        messages: Decoded FIT messages.

    Returns:
        List of lap summary dicts (may be empty).
    """
    lap_mesgs = messages.get("lap_mesgs", [])
    laps = []
    for lap in lap_mesgs:
        lap_data: dict[str, Any] = {}
        start = lap.get("start_time")
        if isinstance(start, datetime):
            lap_data["started_at"] = start.astimezone(timezone.utc).isoformat()

        lap_data["distance_meters"] = _round_or_none(lap.get("total_distance"), 1)
        lap_data["duration_seconds"] = _round_or_none(lap.get("total_elapsed_time"), 1)
        lap_data["average_heart_rate"] = lap.get("avg_heart_rate")
        lap_data["max_heart_rate"] = lap.get("max_heart_rate")
        lap_data["average_cadence"] = (
            lap.get("avg_running_cadence") or lap.get("avg_cadence")
        )

        dist = lap_data.get("distance_meters")
        dur = lap_data.get("duration_seconds")
        if dist and dur and dist > 0:
            lap_data["average_pace_per_km"] = round(dur / (dist / 1000), 1)

        laps.append({k: v for k, v in lap_data.items() if v is not None})

    logger.debug("Extracted %d laps", len(laps))
    return laps


def _extract_zones_target(messages: dict[str, Any]) -> dict[str, Any]:
    """Extract zones_target message fields (includes Garmin LTHR).

    Args:
        messages: Decoded FIT messages.

    Returns:
        Dict with threshold_heart_rate and related fields, or empty dict.
    """
    zt_mesgs = messages.get("zones_target_mesgs", [])
    if not zt_mesgs:
        return {}

    zt = zt_mesgs[0]
    result: dict[str, Any] = {}

    thr = zt.get("threshold_heart_rate")
    if thr is not None:
        result["threshold_heart_rate"] = thr
        logger.info("LTHR from FIT file zones_target: %d bpm", thr)

    ftp = zt.get("functional_threshold_power")
    if ftp is not None:
        result["functional_threshold_power"] = ftp

    max_hr = zt.get("max_heart_rate")
    if max_hr is not None:
        result["max_heart_rate"] = max_hr

    return result


def _round_or_none(value: Any, decimals: int = 0) -> float | int | None:
    """Round a numeric value or return None if absent."""
    if value is None:
        return None
    try:
        rounded = round(float(value), decimals)
        return int(rounded) if decimals == 0 else rounded
    except (TypeError, ValueError):
        return None


def _to_epoch(ts: Any) -> float | None:
    """Convert a datetime or numeric timestamp to a Unix epoch float."""
    if isinstance(ts, datetime):
        return ts.timestamp()
    try:
        return float(ts)
    except (TypeError, ValueError):
        return None


def _elapsed_seconds(first_epoch: float, ts: Any) -> float | None:
    """Compute elapsed seconds from first_epoch to ts."""
    epoch = _to_epoch(ts)
    if epoch is None:
        return None
    return epoch - first_epoch
