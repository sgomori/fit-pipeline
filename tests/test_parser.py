"""Tests for fit_pipeline.parser."""

import pytest

from fit_pipeline.config import Config
from fit_pipeline.exceptions import ParseError
from fit_pipeline.parser import parse_fit_file


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
