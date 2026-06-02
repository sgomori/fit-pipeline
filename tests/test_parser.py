"""Tests for fit_pipeline.parser."""

import pytest

from fit_pipeline.config import Config
from fit_pipeline.exceptions import ParseError
from fit_pipeline.parser import _running_cadence_spm, parse_fit_file


class TestParseErrors:
    def test_missing_file_raises_parse_error(self, base_config: Config) -> None:
        with pytest.raises(ParseError, match="File not found"):
            parse_fit_file("/nonexistent/path/to/file.fit", base_config)

    def test_non_fit_file_raises_parse_error(self, base_config: Config, tmp_path) -> None:
        bad = tmp_path / "not_a_fit.fit"
        bad.write_bytes(b"this is not a fit file")
        with pytest.raises(ParseError):
            parse_fit_file(bad, base_config)


class TestSampleFit:
    """Parser tests against the real anonymized FIT fixture.

    These tests are skipped if tests/fixtures/sample_run.fit does not exist.
    """

    def test_returns_activity_dict(self, sample_fit_path, base_config: Config) -> None:
        result = parse_fit_file(sample_fit_path, base_config)
        assert "activity" in result
        assert isinstance(result["activity"], dict)

    def test_activity_has_required_fields(self, sample_fit_path, base_config: Config) -> None:
        result = parse_fit_file(sample_fit_path, base_config)
        activity = result["activity"]
        assert "started_at" in activity
        assert "distance_meters" in activity
        assert "duration_seconds" in activity

    def test_activity_type_is_running(self, sample_fit_path, base_config: Config) -> None:
        result = parse_fit_file(sample_fit_path, base_config)
        activity = result["activity"]
        assert activity.get("type") in ("running", "run")

    def test_streams_included_when_configured(self, sample_fit_path, base_config: Config) -> None:
        result = parse_fit_file(sample_fit_path, base_config)
        assert "streams" in result
        streams = result["streams"]
        assert isinstance(streams, dict)
        assert len(streams) > 0

    def test_streams_always_extracted_by_parser(self, sample_fit_path, base_config: Config) -> None:
        # Streams are always extracted by the parser for analytics.
        # Exclusion from the final payload is handled by core._build_payload.
        base_config.include_streams = False
        result = parse_fit_file(sample_fit_path, base_config)
        assert "streams" in result

    def test_gps_excluded_by_default(self, sample_fit_path, base_config: Config) -> None:
        result = parse_fit_file(sample_fit_path, base_config)
        streams = result.get("streams", {})
        assert "position_lat" not in streams
        assert "position_long" not in streams

    def test_gps_included_when_configured(self, sample_fit_path, base_config: Config) -> None:
        base_config.exclude_gps = False
        parse_fit_file(sample_fit_path, base_config)
        # GPS may or may not be in this fixture, but no assertion error should occur

    def test_heart_rate_stream_present(self, sample_fit_path, base_config: Config) -> None:
        result = parse_fit_file(sample_fit_path, base_config)
        assert "heart_rate" in result["streams"]
        hr = result["streams"]["heart_rate"]
        assert len(hr) > 0
        assert all(isinstance(v, (int, float)) for v in hr)

    def test_stream_sampling_reduces_count(self, sample_fit_path, base_config: Config) -> None:
        base_config.stream_sample_rate = 1
        result_full = parse_fit_file(sample_fit_path, base_config)

        base_config.stream_sample_rate = 5
        result_sampled = parse_fit_file(sample_fit_path, base_config)

        hr_full = result_full["streams"].get("heart_rate", [])
        hr_sampled = result_sampled["streams"].get("heart_rate", [])

        if len(hr_full) > 10:
            assert len(hr_sampled) < len(hr_full)

    def test_average_pace_computed(self, sample_fit_path, base_config: Config) -> None:
        result = parse_fit_file(sample_fit_path, base_config)
        pace = result["activity"].get("average_pace_per_km")
        assert pace is not None
        assert 120 < pace < 900  # between 2:00/km and 15:00/km

    def test_schema_version_absent_from_parser_output(
        self, sample_fit_path, base_config: Config
    ) -> None:
        # schema_version is added by the pipeline, not the parser
        result = parse_fit_file(sample_fit_path, base_config)
        assert "schema_version" not in result

    def test_laps_is_list(self, sample_fit_path, base_config: Config) -> None:
        result = parse_fit_file(sample_fit_path, base_config)
        assert isinstance(result["laps"], list)

    def test_zones_target_is_dict(self, sample_fit_path, base_config: Config) -> None:
        result = parse_fit_file(sample_fit_path, base_config)
        assert isinstance(result["zones_target"], dict)


class TestRunningCadenceConversion:
    """Running cadence is stored per-leg in rpm; we expose steps per minute."""

    def test_helper_doubles_and_adds_fractional(self) -> None:
        # (88 + 0.289) * 2 = 176.578 -> 177
        assert _running_cadence_spm(88, 0.289) == 177

    def test_helper_handles_missing_fractional(self) -> None:
        assert _running_cadence_spm(91, None) == 182

    def test_helper_returns_none_when_cadence_absent(self) -> None:
        assert _running_cadence_spm(None, 0.5) is None

    def test_activity_cadence_in_steps_per_minute(
        self, sample_fit_path, base_config: Config
    ) -> None:
        activity = parse_fit_file(sample_fit_path, base_config)["activity"]
        # sample_run.fit: avg (88 + 0.289)*2, max (91 + 0.0)*2
        assert activity["average_cadence"] == 177
        assert activity["max_cadence"] == 182

    def test_lap_cadence_in_steps_per_minute(
        self, sample_fit_path, base_config: Config
    ) -> None:
        laps = parse_fit_file(sample_fit_path, base_config)["laps"]
        cadences = [lap["average_cadence"] for lap in laps if "average_cadence" in lap]
        assert cadences  # at least one lap reports cadence
        # Plausible running cadence in spm, not raw per-leg rpm (~85)
        assert all(c > 120 for c in cadences)

    def test_cadence_stream_doubled_and_fractional_dropped(
        self, sample_fit_path, base_config: Config
    ) -> None:
        streams = parse_fit_file(sample_fit_path, base_config)["streams"]
        assert "fractional_cadence" not in streams
        cadence = streams.get("cadence", [])
        assert cadence
        # All steady-state samples land in the spm range, not raw rpm
        assert max(cadence) > 120
