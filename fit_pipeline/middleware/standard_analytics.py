"""StandardAnalyticsProcessor — computes training metrics from parsed FIT data.

All metrics return None (not an error) when required stream data or
configuration is absent. A warning is logged for expected but missing inputs
(e.g. LTHR not configured). The processor never raises on missing data.
"""

import logging
import math
import statistics
from typing import Any

from fit_pipeline.config import Config
from fit_pipeline.processor import Processor

logger = logging.getLogger(__name__)

# LTHR-based zone upper boundaries (as % of LTHR), following Joe Friel's
# published running zones compressed to 5 buckets:
#   Z1 <85% | Z2 85-89% | Z3 90-99% | Z4 100-106% | Z5 >106% (unbounded)
_LTHR_ZONE_PCTS = [0.85, 0.89, 0.99, 1.06]

# Banister TRIMP exponential coefficients by gender
_TRIMP_COEFFICIENTS = {
    "male": (0.64, 1.92),
    "female": (0.86, 1.67),
}

# GAP polynomial constants (Strava-style approximation)
_GAP_UPHILL_K = 0.033
_GAP_DOWNHILL_K = 0.018
_GAP_DOWNHILL_MAX_GRADE = -15.0  # cap downhill grade for adjustment

# Speed at or below this (m/s, ≈1.8 km/h) is treated as stopped/standing and
# excluded from pace- and speed-based aggregates. A stopped sample is "infinite
# pace", not zero pace, so it must not be folded into means as a data point.
_MIN_MOVING_SPEED_M_S = 0.5


class StandardAnalyticsProcessor(Processor):
    """Compute standard training analytics from parsed FIT activity data.

    Adds a ``computed_metrics`` key to the payload dict. Requires ``activity``
    and ``streams`` keys to be present (populated by the parser).

    Metrics computed:
        - aerobic_decoupling_pct
        - efficiency_factor
        - cardiac_drift_bpm
        - tss_score (hrTSS)
        - rtss_score (rTSS from Normalized Graded Pace)
        - pace_cv
        - hr_zone_distribution
        - pace_zone_distribution
        - trimp
        - avg_grade_adjusted_pace_per_km / grade_adjusted_efficiency_factor

    All metrics are None if the required streams or config are absent.
    """

    def __init__(self, config: Config) -> None:
        """Initialize with pipeline config.

        Args:
            config: Loaded pipeline configuration.
        """
        super().__init__(config)

    def process(self, data: dict[str, Any]) -> dict[str, Any]:
        """Compute analytics metrics and attach them to the payload.

        Args:
            data: Activity payload dict (must contain ``activity`` and
                optionally ``streams`` and ``zones_target``).

        Returns:
            Payload with ``computed_metrics`` key added.
        """
        activity = data.get("activity", {})
        streams = data.get("streams", {})
        zones_target = data.get("zones_target", {})

        lthr = self._resolve_lthr(activity, zones_target)

        hr_stream = streams.get("heart_rate") or []
        speed_stream = self._get_speed_stream(streams)  # m/s, None when stopped
        pace_stream = self._get_pace_stream(streams)
        altitude_stream = streams.get("enhanced_altitude") or streams.get("altitude") or []
        distance_stream = streams.get("distance") or []

        avg_hr = self._safe_mean(hr_stream)
        avg_speed_m_s = self._safe_mean(speed_stream)  # arithmetic mean of speed
        avg_speed_m_per_min = avg_speed_m_s * 60 if avg_speed_m_s is not None else None

        duration_s = activity.get("moving_time_seconds") or activity.get("duration_seconds")

        metrics: dict[str, Any] = {}

        metrics["aerobic_decoupling_pct"] = self._aerobic_decoupling(
            pace_stream, hr_stream
        )
        metrics["efficiency_factor"] = self._efficiency_factor(
            avg_speed_m_per_min, avg_hr
        )
        metrics["cardiac_drift_bpm"] = self._cardiac_drift(hr_stream)
        metrics["tss_score"] = self._tss(avg_hr, lthr, duration_s)
        metrics["rtss_score"] = self._rtss(
            speed_stream, altitude_stream, distance_stream, duration_s
        )
        metrics["pace_cv"] = self._pace_cv(pace_stream)
        metrics["hr_zone_distribution"] = self._hr_zone_distribution(
            hr_stream, lthr
        )
        metrics["pace_zone_distribution"] = self._pace_zone_distribution(pace_stream)

        max_hr = self._resolve_max_hr(zones_target)
        metrics["trimp"] = self._trimp(avg_hr, max_hr, duration_s)

        gap_metrics = self._grade_adjusted_pace(
            speed_stream, altitude_stream, distance_stream, avg_hr
        )
        metrics.update(gap_metrics)

        data["computed_metrics"] = metrics
        logger.debug(
            "Computed %d metrics (%d null)",
            len(metrics),
            sum(1 for v in metrics.values() if v is None),
        )
        return data

    # ------------------------------------------------------------------
    # LTHR resolution
    # ------------------------------------------------------------------

    def _resolve_lthr(self, activity: dict[str, Any], zones_target: dict[str, Any]) -> int | None:
        """Resolve lactate threshold HR from FIT file then config fallback.

        Priority:
            1. zones_target.threshold_heart_rate (Garmin auto-detected)
            2. config.threshold_hr
            3. None → dependent metrics return null

        A watch that has never auto-detected an LTHR writes the field as zero
        rather than omitting it, so presence alone does not mean a usable value.
        Zero has to fall through to the config fallback: taken literally it is a
        divide-by-zero in hrTSS, which fails the whole activity.

        Args:
            activity: Session summary dict.
            zones_target: Extracted zones_target FIT message fields.

        Returns:
            LTHR in BPM, or None.
        """
        fit_lthr = zones_target.get("threshold_heart_rate")
        if fit_lthr:
            logger.debug("Using LTHR from FIT file zones_target: %d bpm", fit_lthr)
            return int(fit_lthr)

        if self.config.threshold_hr is not None:
            logger.debug("Using LTHR from config: %d bpm", self.config.threshold_hr)
            return self.config.threshold_hr

        logger.warning(
            "No LTHR available (zones_target absent, THRESHOLD_HR not configured). "
            "tss_score and hr_zone_distribution will be null."
        )
        return None

    def _resolve_max_hr(self, zones_target: dict[str, Any]) -> int | None:
        """Resolve the physiological max HR for TRIMP from config then FIT file.

        Banister TRIMP needs the individual's physiological maximum, not the
        peak observed in a single session, so the session ``max_heart_rate`` is
        deliberately not used as a fallback.

        Priority:
            1. config.max_hr (athlete-known maximum)
            2. zones_target.max_heart_rate (Garmin profile maximum)
            3. None → TRIMP returns null

        Args:
            zones_target: Extracted zones_target FIT message fields.

        Returns:
            Max HR in BPM, or None.
        """
        if self.config.max_hr is not None:
            logger.debug("Using max HR from config: %d bpm", self.config.max_hr)
            return self.config.max_hr

        fit_max = zones_target.get("max_heart_rate")
        if fit_max is not None:
            logger.debug("Using max HR from FIT file zones_target: %d bpm", fit_max)
            return int(fit_max)

        return None

    # ------------------------------------------------------------------
    # Metric computations
    # ------------------------------------------------------------------

    def _aerobic_decoupling(
        self, pace_stream: list[float | None], hr_stream: list[float]
    ) -> float | None:
        """Compute aerobic decoupling as Pa:HR drift between activity halves.

        Uses speed/HR ratio (not pace/HR) to match TrainingPeaks convention.
        Positive result = HR drifted up relative to speed (decoupling).
        < 5% = aerobically efficient.

        Args:
            pace_stream: List of pace values in s/km.
            hr_stream: List of heart rate values in BPM.

        Returns:
            Decoupling percentage, or None if streams are insufficient.
        """
        n = min(len(pace_stream), len(hr_stream))
        if n < 4:
            logger.warning("Insufficient data for aerobic decoupling (need ≥ 4 paired records)")
            return None

        paired_pace = pace_stream[:n]
        paired_hr = hr_stream[:n]

        mid = n // 2
        h1_pace = paired_pace[:mid]
        h1_hr = paired_hr[:mid]
        h2_pace = paired_pace[mid:]
        h2_hr = paired_hr[mid:]

        eff_h1 = self._efficiency_ratio(h1_pace, h1_hr)
        eff_h2 = self._efficiency_ratio(h2_pace, h2_hr)

        if eff_h1 is None or eff_h2 is None or eff_h1 == 0:
            return None

        decoupling = (eff_h1 - eff_h2) / eff_h1 * 100
        logger.debug(
            "Aerobic decoupling: eff_h1=%.4f eff_h2=%.4f → %.2f%%",
            eff_h1,
            eff_h2,
            decoupling,
        )
        return round(decoupling, 2)

    def _efficiency_ratio(
        self, pace_slice: list[float | None], hr_slice: list[float]
    ) -> float | None:
        """Compute avg_speed_m_per_min / avg_hr for a sub-sequence.

        Args:
            pace_slice: Pace values in s/km.
            hr_slice: Heart rate values in BPM.

        Returns:
            Efficiency ratio, or None.
        """
        avg_pace = self._safe_mean(pace_slice)
        avg_hr = self._safe_mean(hr_slice)
        speed = _pace_to_speed(avg_pace)
        if speed is None or avg_hr is None or avg_hr == 0:
            return None
        return speed / avg_hr

    def _efficiency_factor(
        self, avg_speed_m_per_min: float | None, avg_hr: float | None
    ) -> float | None:
        """Compute Efficiency Factor = avg_speed_m_per_min / avg_heart_rate.

        Args:
            avg_speed_m_per_min: Average speed in metres per minute.
            avg_hr: Average heart rate in BPM.

        Returns:
            EF (unitless, expected range ~1.2–1.8 for trained runners), or None.
        """
        if avg_speed_m_per_min is None or avg_hr is None or avg_hr == 0:
            return None
        ef = avg_speed_m_per_min / avg_hr
        logger.debug("Efficiency factor: %.4f", ef)
        return round(ef, 4)

    def _cardiac_drift(self, hr_stream: list[float]) -> int | None:
        """Compute cardiac drift as Q4 minus Q1 average HR.

        Args:
            hr_stream: Full heart rate stream.

        Returns:
            Drift in BPM (positive = fatigue/heat accumulation), or None.
        """
        n = len(hr_stream)
        if n < 8:
            logger.warning(
                "Insufficient HR data for cardiac drift (need ≥ 8 records, got %d)", n
            )
            return None

        quarter = n // 4
        q1_avg = self._safe_mean(hr_stream[:quarter])
        q4_avg = self._safe_mean(hr_stream[n - quarter:])

        if q1_avg is None or q4_avg is None:
            return None

        drift = round(q4_avg - q1_avg)
        logger.debug("Cardiac drift: Q1=%.1f Q4=%.1f → %d bpm", q1_avg, q4_avg, drift)
        return drift

    def _tss(
        self,
        avg_hr: float | None,
        lthr: int | None,
        duration_s: float | None,
    ) -> float | None:
        """Compute hrTSS (heart rate Training Stress Score).

        Formula: hrTSS = (duration_seconds × IF²) / 3600 × 100
        where IF = avg_heart_rate / LTHR

        Args:
            avg_hr: Average heart rate in BPM.
            lthr: Lactate threshold heart rate in BPM.
            duration_s: Activity duration in seconds.

        Returns:
            hrTSS score, or None if LTHR or required fields are absent.
        """
        if lthr is None or avg_hr is None or duration_s is None or duration_s <= 0:
            return None

        intensity_factor = avg_hr / lthr
        tss = (duration_s * intensity_factor ** 2) / 3600 * 100
        logger.debug("hrTSS: IF=%.3f duration=%.0fs → %.1f", intensity_factor, duration_s, tss)
        return round(tss, 1)

    def _rtss(
        self,
        speed_stream: list[float | None],
        altitude_stream: list[float],
        distance_stream: list[float],
        duration_s: float | None,
    ) -> float | None:
        """Compute rTSS (run Training Stress Score) from Normalized Graded Pace.

        Unlike hrTSS (which uses average HR and cannot reward variability),
        rTSS uses Normalized Graded Pace: grade-adjusted speed is smoothed over
        a 30-second rolling window, then normalized via the 4th-power mean so
        that surges are weighted more heavily.

        Formula: IF = NGP_speed / threshold_speed;
        rTSS = (duration_seconds × IF²) / 3600 × 100.

        Args:
            speed_stream: Speed in m/s with None for stopped samples.
            altitude_stream: Altitude in metres.
            distance_stream: Cumulative distance in metres.
            duration_s: Activity duration in seconds.

        Returns:
            rTSS score, or None if THRESHOLD_PACE is not configured or data
            is insufficient.
        """
        if self.config.threshold_pace is None:
            logger.debug("THRESHOLD_PACE not configured; rtss_score is null")
            return None
        if duration_s is None or duration_s <= 0:
            return None

        gap_speeds = self._grade_adjusted_speed_series(
            speed_stream, altitude_stream, distance_stream
        )
        if not gap_speeds:
            return None

        # Rolling 30 s average of grade-adjusted speed (window in samples).
        window = max(1, round(30 / self.config.stream_sample_rate))
        rolling: list[float] = []
        for i in range(len(gap_speeds)):
            chunk = gap_speeds[max(0, i - window + 1): i + 1]
            rolling.append(statistics.mean(chunk))

        # Normalized graded speed = 4th root of the mean of 4th powers.
        ngp_speed = float(statistics.mean(r ** 4 for r in rolling) ** 0.25)

        threshold_speed = 1000 / self.config.threshold_pace  # m/s
        intensity_factor = ngp_speed / threshold_speed
        rtss = (duration_s * intensity_factor ** 2) / 3600 * 100
        logger.debug(
            "rTSS: NGP=%.3f m/s IF=%.3f duration=%.0fs → %.1f",
            ngp_speed, intensity_factor, duration_s, rtss,
        )
        return round(rtss, 1)

    def _pace_cv(self, pace_stream: list[float | None]) -> float | None:
        """Compute the coefficient of variation of pace = std(pace) / mean(pace).

        This is a pace-dispersion statistic (higher = more variable effort:
        trail, intervals; lower = steady road run). It is NOT the Coggan
        Variability Index (Normalized Power / Average Power), which is a
        different metric on a ~1.0–1.3 scale.

        Args:
            pace_stream: Pace values in s/km (None for stopped samples).

        Returns:
            Pace CV (unitless), or None.
        """
        clean = [p for p in pace_stream if p is not None]
        if len(clean) < 2:
            return None
        mean_pace = self._safe_mean(clean)
        if mean_pace is None or mean_pace == 0:
            return None
        try:
            std_pace = statistics.stdev(clean)
        except statistics.StatisticsError:
            return None
        cv = std_pace / mean_pace
        logger.debug("Pace CV: std=%.2f mean=%.2f → %.4f", std_pace, mean_pace, cv)
        return round(cv, 4)

    def _hr_zone_distribution(
        self, hr_stream: list[float], lthr: int | None
    ) -> dict[str, float] | None:
        """Compute time-in-zone percentages using LTHR-based boundaries.

        Zone boundaries default to LTHR percentages (Friel running zones,
        compressed to 5 buckets). Override with HR_ZONE_1–HR_ZONE_5 config
        (BPM upper boundaries).

        Args:
            hr_stream: Heart rate stream in BPM.
            lthr: Lactate threshold heart rate. None → return None.

        Returns:
            Dict with zone_1–zone_5 percentages, or None.
        """
        if not hr_stream:
            return None
        if lthr is None:
            return None

        boundaries = self._hr_zone_boundaries(lthr)
        counts = [0, 0, 0, 0, 0]
        total = len(hr_stream)

        for hr in hr_stream:
            if hr is None:
                total -= 1
                continue
            zone_idx = 4  # default to zone 5
            for i, boundary in enumerate(boundaries):
                if hr <= boundary:
                    zone_idx = i
                    break
            counts[zone_idx] += 1

        if total == 0:
            return None

        distribution = {
            f"zone_{i + 1}": round(counts[i] / total * 100, 1)
            for i in range(5)
        }
        logger.debug("HR zone distribution: %s", distribution)
        return distribution

    def _hr_zone_boundaries(self, lthr: int) -> list[float]:
        """Return the upper BPM boundary for zones 1–4.

        Uses config overrides if set, otherwise computes from LTHR percentages.
        Zone 5 has no upper boundary.

        Args:
            lthr: Lactate threshold heart rate in BPM.

        Returns:
            List of 4 upper boundaries (BPM) for zones 1–4.
        """
        # hr_zone_5 has no upper boundary — Zone 5 is unbounded by definition.
        # All four of zones 1-4 must be set to use config overrides; partial
        # overrides fall back to LTHR percentages to keep boundaries consistent.
        config_boundaries = [
            self.config.hr_zone_1,
            self.config.hr_zone_2,
            self.config.hr_zone_3,
            self.config.hr_zone_4,
        ]
        if all(b is not None for b in config_boundaries):
            return [float(b) for b in config_boundaries]  # type: ignore[arg-type]

        return [lthr * pct for pct in _LTHR_ZONE_PCTS]

    def _pace_zone_distribution(
        self, pace_stream: list[float | None]
    ) -> dict[str, float] | None:
        """Compute time-in-pace-zone percentages.

        Requires PACE_ZONE_EASY, PACE_ZONE_MODERATE, PACE_ZONE_THRESHOLD to be
        configured. Returns None if none are set.

        Zone definitions (pace in s/km — lower value = faster):
            hard:      pace ≤ PACE_ZONE_THRESHOLD (fastest)
            threshold: PACE_ZONE_THRESHOLD < pace ≤ PACE_ZONE_MODERATE
            moderate:  PACE_ZONE_MODERATE < pace ≤ PACE_ZONE_EASY
            easy:      pace > PACE_ZONE_EASY (slowest)

        Args:
            pace_stream: Pace values in s/km.

        Returns:
            Dict with easy/moderate/threshold/hard percentages, or None.
        """
        easy_boundary = self.config.pace_zone_easy
        moderate_boundary = self.config.pace_zone_moderate
        threshold_boundary = self.config.pace_zone_threshold

        if easy_boundary is None and moderate_boundary is None and threshold_boundary is None:
            logger.debug("Pace zone boundaries not configured; pace_zone_distribution is null")
            return None

        if not pace_stream:
            return None

        counts = {"easy": 0, "moderate": 0, "threshold": 0, "hard": 0}
        total = 0

        for pace in pace_stream:
            if pace is None:
                continue
            total += 1
            if threshold_boundary is not None and pace <= threshold_boundary:
                counts["hard"] += 1
            elif moderate_boundary is not None and pace <= moderate_boundary:
                counts["threshold"] += 1
            elif easy_boundary is not None and pace <= easy_boundary:
                counts["moderate"] += 1
            else:
                counts["easy"] += 1

        if total == 0:
            return None

        distribution = {k: round(v / total * 100, 1) for k, v in counts.items()}
        logger.debug("Pace zone distribution: %s", distribution)
        return distribution

    def _trimp(
        self,
        avg_hr: float | None,
        max_hr: int | None,
        duration_s: float | None,
    ) -> float | None:
        """Compute Banister TRIMP (Training Impulse).

        Formula: TRIMP = duration_min × hrr × c1 × e^(c2 × hrr)
        where hrr = (avg_hr - resting_hr) / (max_hr - resting_hr)
        and (c1, c2) are gender-specific Banister coefficients.

        Args:
            avg_hr: Average heart rate in BPM.
            max_hr: Physiological max HR in BPM (resolved by _resolve_max_hr).
            duration_s: Activity duration in seconds.

        Returns:
            TRIMP value, or None if resting or max HR is unavailable.
        """
        resting_hr = self.config.resting_hr
        if resting_hr is None:
            logger.debug("RESTING_HR not configured; TRIMP is null")
            return None

        if avg_hr is None or duration_s is None or duration_s <= 0:
            return None

        if max_hr is None:
            logger.warning(
                "No max HR available (MAX_HR not configured, absent from FIT); TRIMP is null"
            )
            return None

        hr_range = float(max_hr) - float(resting_hr)
        if hr_range <= 0:
            logger.warning("max_hr ≤ resting_hr; TRIMP is null")
            return None

        hrr = (avg_hr - resting_hr) / hr_range
        hrr = max(0.0, min(1.0, hrr))  # clamp to [0, 1]

        gender = self.config.trimp_gender
        c1, c2 = _TRIMP_COEFFICIENTS.get(gender, _TRIMP_COEFFICIENTS["male"])

        duration_min = duration_s / 60
        trimp = duration_min * hrr * c1 * math.exp(c2 * hrr)
        logger.debug(
            "TRIMP: hrr=%.3f duration=%.1f min c1=%.2f c2=%.2f → %.1f",
            hrr, duration_min, c1, c2, trimp,
        )
        return round(trimp, 1)

    @staticmethod
    def _grade_adjusted_speed_series(
        speed_stream: list[float | None],
        altitude_stream: list[float],
        distance_stream: list[float],
    ) -> list[float]:
        """Return per-record grade-adjusted speed (m/s) for moving samples.

        Applies a Strava-style adjustment to each record's flat speed:
            uphill:   factor = 1 + 0.033 × grade_pct
            downhill: factor = 1 - 0.018 × |grade_pct| (grade capped at -15%)
        grade_pct = (alt_diff_m / dist_diff_m) × 100 per segment. The
        equivalent-flat speed is ``speed × factor`` (faster on flat than uphill).
        Stopped samples (speed None) and segments without a valid distance delta
        are skipped.

        Args:
            speed_stream: Speed in m/s with None for stopped samples.
            altitude_stream: Altitude in metres.
            distance_stream: Cumulative distance in metres.

        Returns:
            List of grade-adjusted speeds in m/s (may be shorter than input).
        """
        n = min(len(speed_stream), len(altitude_stream), len(distance_stream))
        gap_speeds: list[float] = []

        for i in range(n):
            speed = speed_stream[i]
            if speed is None:
                continue

            if i == 0:
                gap_speeds.append(speed)
                continue

            alt_diff = (altitude_stream[i] or 0) - (altitude_stream[i - 1] or 0)
            dist_diff = (distance_stream[i] or 0) - (distance_stream[i - 1] or 0)

            if dist_diff <= 0:
                gap_speeds.append(speed)
                continue

            grade_pct = (alt_diff / dist_diff) * 100

            if grade_pct >= 0:
                factor = 1 + _GAP_UPHILL_K * grade_pct
            else:
                capped_grade = max(grade_pct, _GAP_DOWNHILL_MAX_GRADE)
                factor = 1 - _GAP_DOWNHILL_K * abs(capped_grade)

            factor = max(0.5, factor)  # sanity floor (uphill guard)
            gap_speeds.append(speed * factor)

        return gap_speeds

    def _grade_adjusted_pace(
        self,
        speed_stream: list[float | None],
        altitude_stream: list[float],
        distance_stream: list[float],
        avg_hr: float | None,
    ) -> dict[str, Any]:
        """Compute grade-adjusted pace metrics from the speed stream.

        Averages grade-adjusted *speed* arithmetically (the time-weighted mean
        the GAP/EF literature intends), then reports the equivalent pace.

        Args:
            speed_stream: Speed in m/s with None for stopped samples.
            altitude_stream: Altitude in metres.
            distance_stream: Cumulative distance in metres.
            avg_hr: Average heart rate in BPM (for GAP efficiency factor).

        Returns:
            Dict with avg_grade_adjusted_pace_per_km and
            grade_adjusted_efficiency_factor (both None if data absent).
        """
        null_result: dict[str, Any] = {
            "avg_grade_adjusted_pace_per_km": None,
            "grade_adjusted_efficiency_factor": None,
        }

        if min(len(speed_stream), len(altitude_stream), len(distance_stream)) < 2:
            logger.debug("Insufficient data for GAP (need speed + altitude + distance streams)")
            return null_result

        gap_speeds = self._grade_adjusted_speed_series(
            speed_stream, altitude_stream, distance_stream
        )
        if not gap_speeds:
            return null_result

        avg_gap_speed_m_s = statistics.mean(gap_speeds)
        if avg_gap_speed_m_s <= 0:
            return null_result

        avg_gap_pace = 1000 / avg_gap_speed_m_s  # s/km

        gap_ef: float | None = None
        if avg_hr and avg_hr > 0:
            gap_ef = round(avg_gap_speed_m_s * 60 / avg_hr, 4)

        logger.debug(
            "GAP: avg_gap_speed=%.3f m/s avg_gap_pace=%.1f s/km gap_ef=%s",
            avg_gap_speed_m_s,
            avg_gap_pace,
            gap_ef,
        )

        return {
            "avg_grade_adjusted_pace_per_km": round(avg_gap_pace, 1),
            "grade_adjusted_efficiency_factor": gap_ef,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_mean(values: list[Any]) -> float | None:
        """Return mean of a non-empty list of numerics, or None."""
        clean = [v for v in values if v is not None]
        if not clean:
            return None
        return float(statistics.mean(clean))

    @staticmethod
    def _get_speed_stream(streams: dict[str, Any]) -> list[float | None]:
        """Return the speed stream in m/s, with None for stopped/missing samples.

        Samples at or below ``_MIN_MOVING_SPEED_M_S`` are treated as stopped and
        returned as None so they are skipped by pace/speed aggregates rather
        than counted as real (very fast) data points.

        Args:
            streams: Parsed stream dict (prefers ``enhanced_speed``).

        Returns:
            List of m/s floats with None for stopped/missing entries.
        """
        speed = streams.get("enhanced_speed") or streams.get("speed") or []
        return [
            float(v) if v is not None and v > _MIN_MOVING_SPEED_M_S else None
            for v in speed
        ]

    @classmethod
    def _get_pace_stream(cls, streams: dict[str, Any]) -> list[float | None]:
        """Return pace stream in s/km, deriving from speed.

        Stopped/missing samples are None (not 0.0), since a stopped sample is
        "infinite pace" and must not be averaged in as a real value.

        Args:
            streams: Parsed stream dict.

        Returns:
            List of s/km floats with None for stopped/missing entries.
        """
        return [
            round(1000 / v, 2) if v is not None else None
            for v in cls._get_speed_stream(streams)
        ]


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _pace_to_speed(pace_s_per_km: float | None) -> float | None:
    """Convert pace in s/km to speed in m/min.

    Args:
        pace_s_per_km: Pace in seconds per kilometre.

    Returns:
        Speed in metres per minute, or None.
    """
    if pace_s_per_km is None or pace_s_per_km <= 0:
        return None
    return 1000 / pace_s_per_km * 60  # (1000 m/km) / (pace s/km) * (60 s/min)
