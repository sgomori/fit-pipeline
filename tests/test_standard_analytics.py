"""Tests for StandardAnalyticsProcessor.

Unit tests use synthetic data streams with known expected outputs.
Integration tests against the real FIT fixture are skipped if it's absent.
"""

import json
import math

import pytest

from fit_pipeline.config import Config
from fit_pipeline.middleware.standard_analytics import (
    StandardAnalyticsProcessor,
)
from tests.conftest import SAMPLE_EXPECTED

# ---------------------------------------------------------------------------
# Helpers for building synthetic test payloads
# ---------------------------------------------------------------------------

def _make_payload(
    hr: list[float] | None = None,
    speed_m_per_s: list[float] | None = None,
    altitude: list[float] | None = None,
    distance: list[float] | None = None,
    duration_s: float = 3600,
    avg_hr: float = 150,
    max_hr: int = 185,
    threshold_hr: int | None = None,
    zones_target_lthr: int | None = None,
) -> dict:
    """Build a minimal synthetic payload for analytics tests."""
    speed = speed_m_per_s or []
    streams: dict = {}
    if hr is not None:
        streams["heart_rate"] = hr
    if speed:
        streams["speed"] = speed
    if altitude is not None:
        streams["altitude"] = altitude
    if distance is not None:
        streams["distance"] = distance

    payload: dict = {
        "activity": {
            "moving_time_seconds": duration_s,
            "avg_heart_rate": avg_hr,
            "max_heart_rate": max_hr,
        },
        "streams": streams,
        "zones_target": (
            {"threshold_heart_rate": zones_target_lthr}
            if zones_target_lthr is not None
            else {}
        ),
    }
    if threshold_hr is not None:
        payload["_test_threshold_hr"] = threshold_hr
    return payload


def _make_processor(config: Config) -> StandardAnalyticsProcessor:
    return StandardAnalyticsProcessor(config)


# ---------------------------------------------------------------------------
# Aerobic decoupling
# ---------------------------------------------------------------------------

class TestAerobicDecoupling:
    def test_returns_none_with_insufficient_data(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        result = proc._aerobic_decoupling([], [])
        assert result is None

    def test_zero_decoupling_when_perfectly_consistent(self, analytics_config: Config) -> None:
        # Constant pace and HR → no decoupling
        hr = [150.0] * 100
        speed = [3.5] * 100  # m/s
        pace = [round(1000 / s, 2) for s in speed]
        proc = _make_processor(analytics_config)
        result = proc._aerobic_decoupling(pace, hr)
        assert result is not None
        assert abs(result) < 0.01

    def test_positive_decoupling_when_hr_drifts_up(self, analytics_config: Config) -> None:
        # First half: low HR, second half: same pace but higher HR
        n = 100
        pace = [300.0] * n  # constant 5:00/km
        hr = [140.0] * (n // 2) + [160.0] * (n // 2)  # HR drifts up
        proc = _make_processor(analytics_config)
        result = proc._aerobic_decoupling(pace, hr)
        assert result is not None
        assert result > 0

    def test_negative_decoupling_when_runner_warms_up(self, analytics_config: Config) -> None:
        # First half: higher HR, second half: same pace, lower HR (warm-up effect)
        n = 100
        pace = [300.0] * n
        hr = [160.0] * (n // 2) + [140.0] * (n // 2)
        proc = _make_processor(analytics_config)
        result = proc._aerobic_decoupling(pace, hr)
        assert result is not None
        assert result < 0


# ---------------------------------------------------------------------------
# Efficiency factor
# ---------------------------------------------------------------------------

class TestEfficiencyFactor:
    def test_returns_none_without_streams(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._efficiency_factor(None, None) is None
        assert proc._efficiency_factor(180.0, None) is None

    def test_returns_none_with_zero_hr(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._efficiency_factor(180.0, 0.0) is None

    def test_known_value(self, analytics_config: Config) -> None:
        # speed = 3.16 m/s → 189.6 m/min; avg HR = 150
        # EF = 189.6 / 150 = 1.264
        proc = _make_processor(analytics_config)
        speed_m_per_min = 3.16 * 60
        result = proc._efficiency_factor(speed_m_per_min, 150.0)
        assert result is not None
        assert abs(result - 1.264) < 0.01

    def test_typical_range(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        # Trained runner: ~3.5 m/s, ~145 bpm
        result = proc._efficiency_factor(3.5 * 60, 145.0)
        assert result is not None
        assert 1.0 < result < 2.0


# ---------------------------------------------------------------------------
# Cardiac drift
# ---------------------------------------------------------------------------

class TestCardiacDrift:
    def test_returns_none_with_too_few_records(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._cardiac_drift([150] * 7) is None

    def test_zero_drift_with_constant_hr(self, analytics_config: Config) -> None:
        hr = [150.0] * 100
        proc = _make_processor(analytics_config)
        assert proc._cardiac_drift(hr) == 0

    def test_positive_drift_when_hr_increases(self, analytics_config: Config) -> None:
        n = 100
        hr = [140.0] * (n // 4) + [148.0] * (n // 2) + [160.0] * (n // 4)
        proc = _make_processor(analytics_config)
        result = proc._cardiac_drift(hr)
        assert result is not None
        assert result > 0

    def test_known_drift_value(self, analytics_config: Config) -> None:
        # Q1 avg = 140, Q4 avg = 155 → drift = 15
        hr = [140.0] * 25 + [148.0] * 50 + [155.0] * 25
        proc = _make_processor(analytics_config)
        result = proc._cardiac_drift(hr)
        assert result == 15


# ---------------------------------------------------------------------------
# TSS (hrTSS)
# ---------------------------------------------------------------------------

class TestTSS:
    def test_returns_none_without_lthr(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._tss(150.0, None, 3600) is None

    def test_returns_none_without_duration(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._tss(150.0, 162, None) is None

    def test_tss_at_threshold_equals_100_per_hour(self, analytics_config: Config) -> None:
        # When avg_hr == LTHR and duration == 3600s, TSS should be 100
        lthr = 162
        proc = _make_processor(analytics_config)
        result = proc._tss(float(lthr), lthr, 3600)
        assert result is not None
        assert abs(result - 100.0) < 0.5

    def test_tss_below_threshold_less_than_100_per_hour(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        result = proc._tss(140.0, 162, 3600)
        assert result is not None
        assert result < 100

    def test_tss_above_threshold_greater_than_100_per_hour(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        result = proc._tss(175.0, 162, 3600)
        assert result is not None
        assert result > 100


# ---------------------------------------------------------------------------
# rTSS (Normalized Graded Pace)
# ---------------------------------------------------------------------------

class TestRTSS:
    def _flat_course(self, speed_m_s: float, n: int) -> tuple[list[float], list[float], list[float]]:
        speed = [speed_m_s] * n
        altitude = [50.0] * n
        distance = [i * speed_m_s for i in range(n)]  # 1 s spacing
        return speed, altitude, distance

    def test_returns_none_without_threshold_pace(self, analytics_config: Config) -> None:
        analytics_config.threshold_pace = None
        proc = _make_processor(analytics_config)
        speed, alt, dist = self._flat_course(3.0, 60)
        assert proc._rtss(speed, alt, dist, 3600) is None

    def test_returns_none_without_duration(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        speed, alt, dist = self._flat_course(3.0, 60)
        assert proc._rtss(speed, alt, dist, None) is None

    def test_at_threshold_flat_equals_100_per_hour(self, analytics_config: Config) -> None:
        # threshold_pace=300 s/km → threshold speed = 1000/300 m/s; steady flat
        # run at exactly that speed for 1 h → IF=1 → rTSS=100.
        analytics_config.threshold_pace = 300
        proc = _make_processor(analytics_config)
        threshold_speed = 1000 / 300
        speed, alt, dist = self._flat_course(threshold_speed, 600)
        result = proc._rtss(speed, alt, dist, 3600)
        assert result is not None
        assert abs(result - 100.0) < 1.0

    def test_faster_than_threshold_exceeds_100_per_hour(self, analytics_config: Config) -> None:
        analytics_config.threshold_pace = 300
        proc = _make_processor(analytics_config)
        faster = 1000 / 270  # 4:30/km, faster than 5:00/km threshold
        speed, alt, dist = self._flat_course(faster, 600)
        result = proc._rtss(speed, alt, dist, 3600)
        assert result is not None
        assert result > 100

    def test_uphill_raises_rtss_vs_flat(self, analytics_config: Config) -> None:
        # Same flat speed, but on a climb → grade-adjusted (NGP) speed is higher
        # → higher rTSS than the flat equivalent.
        analytics_config.threshold_pace = 300
        proc = _make_processor(analytics_config)
        n = 600
        spd = 1000 / 300
        speed = [spd] * n
        flat_alt = [50.0] * n
        climb_alt = [50.0 + i * 0.3 for i in range(n)]
        distance = [i * spd for i in range(n)]
        flat = proc._rtss(speed, flat_alt, distance, 3600)
        climb = proc._rtss(speed, climb_alt, distance, 3600)
        assert flat is not None and climb is not None
        assert climb > flat


# ---------------------------------------------------------------------------
# Pace CV (coefficient of variation)
# ---------------------------------------------------------------------------

class TestPaceCV:
    def test_returns_none_with_empty_stream(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._pace_cv([]) is None

    def test_returns_none_with_single_value(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._pace_cv([300.0]) is None

    def test_ignores_none_samples(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        # None (stopped) samples are excluded, not treated as 0.0
        assert proc._pace_cv([300.0, None, 300.0, None]) == 0.0

    def test_zero_cv_with_constant_pace(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        result = proc._pace_cv([300.0] * 50)
        assert result == 0.0

    def test_higher_cv_for_more_variable_pace(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        steady = [300.0] * 50
        variable = [250.0, 350.0] * 25
        cv_steady = proc._pace_cv(steady)
        cv_variable = proc._pace_cv(variable)
        assert cv_variable is not None
        assert cv_steady is not None
        assert cv_variable > cv_steady


# ---------------------------------------------------------------------------
# HR zone distribution
# ---------------------------------------------------------------------------

class TestHRZoneDistribution:
    def test_returns_none_without_lthr(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._hr_zone_distribution([150] * 100, None) is None

    def test_returns_none_with_empty_stream(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._hr_zone_distribution([], 162) is None

    def test_all_in_zone_1(self, analytics_config: Config) -> None:
        # Zone 1 < 85% of 162 = < 137.7 bpm
        hr = [130.0] * 100
        proc = _make_processor(analytics_config)
        result = proc._hr_zone_distribution(hr, 162)
        assert result is not None
        assert result["zone_1"] == 100.0
        assert result["zone_2"] == 0.0

    def test_all_in_zone_5(self, analytics_config: Config) -> None:
        # Zone 5 > 105% of 162 = > 170.1 bpm
        hr = [175.0] * 100
        proc = _make_processor(analytics_config)
        result = proc._hr_zone_distribution(hr, 162)
        assert result is not None
        assert result["zone_5"] == 100.0

    def test_distribution_sums_to_100(self, analytics_config: Config) -> None:
        hr = [125.0, 140.0, 150.0, 158.0, 165.0, 172.0] * 20
        proc = _make_processor(analytics_config)
        result = proc._hr_zone_distribution(hr, 162)
        assert result is not None
        total = sum(result.values())
        assert abs(total - 100.0) < 0.5


# ---------------------------------------------------------------------------
# Pace zone distribution
# ---------------------------------------------------------------------------

class TestPaceZoneDistribution:
    def test_returns_none_when_no_boundaries_configured(self, base_config: Config) -> None:
        proc = _make_processor(base_config)
        assert proc._pace_zone_distribution([300.0] * 50) is None

    def test_all_easy_when_slow_pace(self, analytics_config: Config) -> None:
        # easy boundary = 360 s/km; pace 400 = slow → all easy
        pace = [400.0] * 100
        proc = _make_processor(analytics_config)
        result = proc._pace_zone_distribution(pace)
        assert result is not None
        assert result["easy"] == 100.0

    def test_all_hard_when_very_fast_pace(self, analytics_config: Config) -> None:
        # threshold boundary = 300 s/km; pace 280 = below → all hard
        pace = [280.0] * 100
        proc = _make_processor(analytics_config)
        result = proc._pace_zone_distribution(pace)
        assert result is not None
        assert result["hard"] == 100.0

    def test_distribution_sums_to_100(self, analytics_config: Config) -> None:
        pace = [280.0, 305.0, 335.0, 370.0] * 25
        proc = _make_processor(analytics_config)
        result = proc._pace_zone_distribution(pace)
        assert result is not None
        total = sum(result.values())
        assert abs(total - 100.0) < 0.5


# ---------------------------------------------------------------------------
# TRIMP
# ---------------------------------------------------------------------------

class TestTRIMP:
    def test_returns_none_without_resting_hr(self, base_config: Config) -> None:
        base_config.resting_hr = None
        proc = _make_processor(base_config)
        assert proc._trimp(150.0, 185, 3600) is None

    def test_returns_none_without_max_hr(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        assert proc._trimp(150.0, None, 3600) is None

    def test_known_male_trimp(self, analytics_config: Config) -> None:
        # resting=48, max=185, avg=150, duration=3600s (60min)
        # hrr = (150-48)/(185-48) = 102/137 = 0.7445
        # trimp = 60 × 0.7445 × 0.64 × e^(1.92 × 0.7445)
        resting = 48
        max_hr = 185
        avg = 150.0
        dur = 3600.0
        hrr = (avg - resting) / (max_hr - resting)
        expected = 60 * hrr * 0.64 * math.exp(1.92 * hrr)

        proc = _make_processor(analytics_config)
        result = proc._trimp(avg, max_hr, dur)
        assert result is not None
        assert abs(result - round(expected, 1)) < 0.5


class TestResolveMaxHr:
    def test_config_takes_priority_over_fit(self, analytics_config: Config) -> None:
        analytics_config.max_hr = 185
        proc = _make_processor(analytics_config)
        assert proc._resolve_max_hr({"max_heart_rate": 200}) == 185

    def test_falls_back_to_fit_physiological_max(self, analytics_config: Config) -> None:
        analytics_config.max_hr = None
        proc = _make_processor(analytics_config)
        # zones_target carries the athlete's profile max, not the session peak
        assert proc._resolve_max_hr({"max_heart_rate": 181}) == 181

    def test_none_when_neither_config_nor_fit(self, analytics_config: Config) -> None:
        analytics_config.max_hr = None
        proc = _make_processor(analytics_config)
        assert proc._resolve_max_hr({}) is None


class TestResolveLthr:
    def test_fit_value_takes_priority_over_config(self, analytics_config: Config) -> None:
        analytics_config.threshold_hr = 150
        proc = _make_processor(analytics_config)
        assert proc._resolve_lthr({}, {"threshold_heart_rate": 168}) == 168

    def test_falls_back_to_config(self, analytics_config: Config) -> None:
        analytics_config.threshold_hr = 162
        proc = _make_processor(analytics_config)
        assert proc._resolve_lthr({}, {}) == 162

    def test_none_when_neither_config_nor_fit(self, analytics_config: Config) -> None:
        analytics_config.threshold_hr = None
        proc = _make_processor(analytics_config)
        assert proc._resolve_lthr({}, {}) is None

    def test_zero_fit_value_falls_through_to_config(self, analytics_config: Config) -> None:
        # A watch that has never auto-detected an LTHR writes the field as zero
        # rather than omitting it. Honouring it divides by zero in hrTSS.
        analytics_config.threshold_hr = 162
        proc = _make_processor(analytics_config)
        assert proc._resolve_lthr({}, {"threshold_heart_rate": 0}) == 162

    def test_zero_fit_value_without_config_yields_none(self, analytics_config: Config) -> None:
        analytics_config.threshold_hr = None
        proc = _make_processor(analytics_config)
        assert proc._resolve_lthr({}, {"threshold_heart_rate": 0}) is None

    def test_zero_lthr_leaves_hr_metrics_null_instead_of_failing(
        self, analytics_config: Config
    ) -> None:
        # The whole point of the fallback: the activity still processes, with
        # the HR-derived metrics explicitly null.
        analytics_config.threshold_hr = None
        payload = _make_payload(
            duration_s=3600,
            avg_hr=150,
            hr=[150] * 60,
            zones_target_lthr=0,
        )
        result = _make_processor(analytics_config).process(payload)
        assert result["computed_metrics"]["tss_score"] is None
        assert result["computed_metrics"]["hr_zone_distribution"] is None


# ---------------------------------------------------------------------------
# Grade-adjusted pace
# ---------------------------------------------------------------------------

class TestGradeAdjustedPace:
    def test_returns_nulls_with_missing_streams(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        result = proc._grade_adjusted_pace([], [], [], 150.0)
        assert result["avg_grade_adjusted_pace_per_km"] is None
        assert result["grade_adjusted_efficiency_factor"] is None

    def test_flat_course_gap_equals_actual_pace(self, analytics_config: Config) -> None:
        # Flat course (constant altitude) → GAP should equal actual pace
        n = 100
        speed = [1000 / 300.0] * n  # 300 s/km in m/s
        altitude = [50.0] * n
        distance = [i * 5.0 for i in range(n)]  # 5m per record
        proc = _make_processor(analytics_config)
        result = proc._grade_adjusted_pace(speed, altitude, distance, 150.0)
        gap = result["avg_grade_adjusted_pace_per_km"]
        assert gap is not None
        assert abs(gap - 300.0) < 1.0

    def test_uphill_gap_is_faster_than_actual_pace(self, analytics_config: Config) -> None:
        # Going uphill at 300 s/km → effort is higher → GAP (flat equivalent) is faster
        n = 50
        speed = [1000 / 300.0] * n  # 300 s/km in m/s
        altitude = [100.0 + i * 0.5 for i in range(n)]  # steady climb
        distance = [i * 10.0 for i in range(n)]  # 10m per record → 5% grade
        proc = _make_processor(analytics_config)
        result = proc._grade_adjusted_pace(speed, altitude, distance, 150.0)
        gap = result["avg_grade_adjusted_pace_per_km"]
        assert gap is not None
        assert gap < 300.0  # GAP < actual pace → faster equivalent

    def test_gap_ef_present_when_hr_available(self, analytics_config: Config) -> None:
        n = 50
        speed = [1000 / 300.0] * n
        altitude = [50.0] * n
        distance = [i * 5.0 for i in range(n)]
        proc = _make_processor(analytics_config)
        result = proc._grade_adjusted_pace(speed, altitude, distance, 150.0)
        assert result["grade_adjusted_efficiency_factor"] is not None

    def test_stopped_samples_excluded_from_pace_stats(self, analytics_config: Config) -> None:
        # Speed stream with several stopped (0 m/s) samples among moving ones.
        streams = {"enhanced_speed": [3.0, 3.0, 0.0, 0.0, 3.0, 3.0]}
        proc = _make_processor(analytics_config)
        pace = proc._get_pace_stream(streams)
        speed = proc._get_speed_stream(streams)
        # Stopped samples become None (not 0.0 / "infinitely fast")
        assert pace == [pytest.approx(1000 / 3.0, abs=0.01), pytest.approx(1000 / 3.0, abs=0.01),
                        None, None, pytest.approx(1000 / 3.0, abs=0.01), pytest.approx(1000 / 3.0, abs=0.01)]
        assert speed.count(None) == 2
        # Mean speed reflects only moving samples (3.0 m/s), not dragged toward 0
        assert proc._safe_mean(speed) == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Full processor integration (synthetic data)
# ---------------------------------------------------------------------------

class TestFullProcessorSynthetic:
    def _make_consistent_payload(self, analytics_config: Config) -> dict:
        n = 200
        hr = [150.0] * n
        speed = [3.33] * n  # m/s → 300 s/km
        altitude = [50.0] * n
        distance = [i * 5.0 for i in range(n)]
        streams = {
            "heart_rate": hr,
            "speed": speed,
            "altitude": altitude,
            "distance": distance,
        }
        return {
            "activity": {
                "moving_time_seconds": 1000.0,
                "avg_heart_rate": 150,
                "max_heart_rate": 185,
            },
            "streams": streams,
            "zones_target": {},
        }

    def test_computed_metrics_key_present(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        payload = self._make_consistent_payload(analytics_config)
        result = proc.process(payload)
        assert "computed_metrics" in result

    def test_all_expected_metric_keys_present(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        payload = self._make_consistent_payload(analytics_config)
        result = proc.process(payload)
        metrics = result["computed_metrics"]
        expected_keys = {
            "aerobic_decoupling_pct",
            "efficiency_factor",
            "cardiac_drift_bpm",
            "tss_score",
            "rtss_score",
            "pace_cv",
            "hr_zone_distribution",
            "pace_zone_distribution",
            "trimp",
            "avg_grade_adjusted_pace_per_km",
            "grade_adjusted_efficiency_factor",
        }
        assert expected_keys.issubset(set(metrics.keys()))

    def test_returns_dict_not_none(self, analytics_config: Config) -> None:
        proc = _make_processor(analytics_config)
        payload = self._make_consistent_payload(analytics_config)
        result = proc.process(payload)
        assert isinstance(result, dict)

    def test_tss_null_when_no_lthr(self, base_config: Config) -> None:
        # No LTHR in config and no zones_target → TSS must be null
        proc = _make_processor(base_config)
        payload = {
            "activity": {"moving_time_seconds": 3600, "avg_heart_rate": 150, "max_heart_rate": 185},
            "streams": {"heart_rate": [150.0] * 100, "speed": [3.33] * 100},
            "zones_target": {},
        }
        result = proc.process(payload)
        assert result["computed_metrics"]["tss_score"] is None

    def test_lthr_from_zones_target_overrides_config(self, analytics_config: Config) -> None:
        # FIT file reports LTHR=170, config has 162 → should use 170
        proc = _make_processor(analytics_config)
        payload = {
            "activity": {"moving_time_seconds": 3600, "avg_heart_rate": 150, "max_heart_rate": 185},
            "streams": {"heart_rate": [150.0] * 100, "speed": [3.33] * 100},
            "zones_target": {"threshold_heart_rate": 170},
        }
        result = proc.process(payload)
        # With LTHR=170: IF = 150/170 = 0.882; TSS ≈ 0.882² × 100 = 77.8
        tss = result["computed_metrics"]["tss_score"]
        assert tss is not None
        assert abs(tss - round((150 / 170) ** 2 * 100, 1)) < 0.5


# ---------------------------------------------------------------------------
# Integration test against real fixture
# ---------------------------------------------------------------------------

class TestSampleFitAnalytics:
    """Analytics integration tests against the anonymized FIT fixture.

    Skipped until the fixture + expected output file are committed.
    """

    def test_metrics_match_expected(self, sample_fit_path, analytics_config: Config) -> None:
        if not SAMPLE_EXPECTED.exists():
            pytest.skip("Expected output fixture not yet available")

        from fit_pipeline.parser import parse_fit_file

        parsed = parse_fit_file(sample_fit_path, analytics_config)
        proc = _make_processor(analytics_config)
        result = proc.process(parsed)
        metrics = result["computed_metrics"]

        with open(SAMPLE_EXPECTED) as f:
            expected = json.load(f)

        expected_metrics = expected.get("computed_metrics", {})
        for key, expected_value in expected_metrics.items():
            if expected_value is None:
                assert metrics.get(key) is None, f"{key}: expected null, got {metrics.get(key)}"
            elif isinstance(expected_value, float):
                actual = metrics.get(key)
                assert actual is not None, f"{key} is null but expected {expected_value}"
                assert abs(actual - expected_value) < 0.5, (
                    f"{key}: expected {expected_value}, got {actual}"
                )
            else:
                assert metrics.get(key) == expected_value, (
                    f"{key}: expected {expected_value}, got {metrics.get(key)}"
                )
