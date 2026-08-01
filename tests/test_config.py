"""Tests for fit_pipeline.config — WEBHOOK_DESTINATIONS parsing and validation."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from fit_pipeline.config import (
    Config,
    WebhookDestination,
    _validate,
    _webhook_destinations,
    load_config,
)
from fit_pipeline.exceptions import ConfigError


@pytest.fixture
def isolated_env() -> Iterator[None]:
    """Snapshot and restore os.environ around a test.

    load_dotenv() writes directly into os.environ, so tests that call
    load_config() must restore the full environment rather than relying on
    monkeypatch, which only tracks variables it set itself.
    """
    saved = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def empty_env_file(tmp_path: Path) -> str:
    """Path to an empty .env file.

    load_config() defaults to searching for a .env next to the package, which
    picks up a developer's real configuration. Tests pass this instead so they
    are independent of the working copy.
    """
    path = tmp_path / "empty.env"
    path.write_text("")
    return str(path)


# Every variable load_config reads. Cleared wholesale so a developer's exported
# shell values cannot influence a test.
CONFIG_ENV_VARS = (
    "WEBHOOK_DESTINATIONS",
    "SERVER_SECRET",
    "SERVER_PORT",
    "UPLOAD_DIR",
    "EXCLUDE_GPS",
    "EXCLUDE_DEVICE_INFO",
    "EXCLUDE_FIELDS",
    "INCLUDE_STREAMS",
    "STREAM_SAMPLE_RATE",
    "DRY_RUN",
    "OUTPUT_FILE",
    "COMPLETED_FILENAME_FORMAT",
    "COMPLETED_SET_MTIME",
    "LOG_LEVEL",
    "LOG_FILE",
    "THRESHOLD_HR",
    "HR_ZONE_1",
    "HR_ZONE_2",
    "HR_ZONE_3",
    "HR_ZONE_4",
    "HR_ZONE_5",
    "PACE_ZONE_EASY",
    "PACE_ZONE_MODERATE",
    "PACE_ZONE_THRESHOLD",
    "THRESHOLD_PACE",
    "RESTING_HR",
    "MAX_HR",
    "TRIMP_GENDER",
)


@pytest.fixture
def clean_env(isolated_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every config env var so load_config sees only the test's inputs."""
    for var in CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestWebhookDestinationsParsing:
    def test_unset_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WEBHOOK_DESTINATIONS", raising=False)
        assert _webhook_destinations() == []

    def test_parses_single_destination(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "WEBHOOK_DESTINATIONS",
            '[{"url": "https://a.example.com/hook", "secret": "sk_a"}]',
        )
        assert _webhook_destinations() == [
            WebhookDestination("https://a.example.com/hook", "sk_a")
        ]

    def test_parses_multiple_destinations_each_with_own_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "WEBHOOK_DESTINATIONS",
            '[{"url": "https://a.example.com/h", "secret": "sk_a"},'
            ' {"url": "https://b.example.com/h", "secret": "sk_b"}]',
        )
        assert _webhook_destinations() == [
            WebhookDestination("https://a.example.com/h", "sk_a"),
            WebhookDestination("https://b.example.com/h", "sk_b"),
        ]

    def test_invalid_json_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_DESTINATIONS", "{not json}")
        with pytest.raises(ConfigError, match="valid JSON"):
            _webhook_destinations()

    def test_non_array_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "WEBHOOK_DESTINATIONS", '{"url": "https://a", "secret": "s"}'
        )
        with pytest.raises(ConfigError, match="JSON array"):
            _webhook_destinations()

    def test_entry_missing_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "WEBHOOK_DESTINATIONS", '[{"url": "https://a.example.com/h"}]'
        )
        with pytest.raises(ConfigError, match="non-empty 'url' and 'secret'"):
            _webhook_destinations()

    def test_entry_missing_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_DESTINATIONS", '[{"secret": "sk_a"}]')
        with pytest.raises(ConfigError, match="non-empty 'url' and 'secret'"):
            _webhook_destinations()

    def test_entry_not_object_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WEBHOOK_DESTINATIONS", '["https://a.example.com/h"]')
        with pytest.raises(ConfigError, match="must be an object"):
            _webhook_destinations()


class TestValidate:
    def test_missing_destinations_raises_when_delivering(self) -> None:
        config = Config(dry_run=False, output_file="", webhook_destinations=[])
        with pytest.raises(ConfigError, match="WEBHOOK_DESTINATIONS"):
            _validate(config)

    def test_dry_run_does_not_require_destinations(self) -> None:
        _validate(Config(dry_run=True, webhook_destinations=[]))  # no raise

    def test_output_file_does_not_require_destinations(self) -> None:
        _validate(Config(dry_run=False, output_file="/tmp/out.json"))  # no raise

    def test_destinations_present_passes(self) -> None:
        config = Config(
            dry_run=False,
            webhook_destinations=[WebhookDestination("https://a.example.com/h", "s")],
        )
        _validate(config)  # no raise


class TestLoadConfigCliOverrides:
    def test_missing_destinations_raises_without_overrides(
        self, clean_env: None, empty_env_file: str
    ) -> None:
        with pytest.raises(ConfigError, match="WEBHOOK_DESTINATIONS"):
            load_config(empty_env_file)

    def test_cli_dry_run_relaxes_destination_requirement(
        self, clean_env: None, empty_env_file: str
    ) -> None:
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.dry_run is True

    def test_cli_output_file_relaxes_destination_requirement(
        self, clean_env: None, empty_env_file: str
    ) -> None:
        config = load_config(empty_env_file, cli_output_file="/tmp/out.json")
        assert config.output_file == "/tmp/out.json"
        assert config.dry_run is False


class TestBlankEnvValues:
    """A variable set to an empty string must be treated as unset.

    .env.example ships optional keys as bare ``KEY=`` lines, so python-dotenv
    puts empty strings into os.environ for every key the user did not fill in.
    """

    def test_blank_optional_int_is_none(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THRESHOLD_HR", "")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.threshold_hr is None

    def test_whitespace_only_int_is_none(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THRESHOLD_HR", "   ")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.threshold_hr is None

    def test_blank_int_falls_back_to_non_none_default(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STREAM_SAMPLE_RATE", "")
        monkeypatch.setenv("SERVER_PORT", "")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.stream_sample_rate == 3
        assert config.server_port == 8000

    def test_padded_int_still_parses(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THRESHOLD_HR", "  162  ")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.threshold_hr == 162

    def test_non_numeric_int_still_raises(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("THRESHOLD_HR", "not_a_number")
        with pytest.raises(ConfigError, match="THRESHOLD_HR must be an integer"):
            load_config(empty_env_file, cli_dry_run=True)

    def test_blank_bool_keeps_true_default(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A blank EXCLUDE_GPS must not silently flip GPS filtering off."""
        monkeypatch.setenv("EXCLUDE_GPS", "")
        monkeypatch.setenv("EXCLUDE_DEVICE_INFO", "")
        monkeypatch.setenv("INCLUDE_STREAMS", "")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.exclude_gps is True
        assert config.exclude_device_info is True
        assert config.include_streams is True

    def test_blank_bool_keeps_false_default(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DRY_RUN", "")
        config = load_config(empty_env_file, cli_output_file="/tmp/out.json")
        assert config.dry_run is False

    def test_explicit_false_bool_overrides_true_default(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXCLUDE_GPS", "false")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.exclude_gps is False

    def test_padded_bool_still_parses(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXCLUDE_GPS", "  TRUE  ")
        monkeypatch.setenv("INCLUDE_STREAMS", " no ")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.exclude_gps is True
        assert config.include_streams is False


class TestBoolParsing:
    """An unrecognized boolean must fail loudly, not resolve to False."""

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_accepted_true_spellings(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        monkeypatch.setenv("EXCLUDE_GPS", value)
        assert load_config(empty_env_file, cli_dry_run=True).exclude_gps is True

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
    def test_accepted_false_spellings(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        monkeypatch.setenv("EXCLUDE_GPS", value)
        assert load_config(empty_env_file, cli_dry_run=True).exclude_gps is False

    @pytest.mark.parametrize("value", ["ture", "y", "t", "enabled", "maybe", "2"])
    def test_typo_raises_rather_than_shipping_gps(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        """A misspelled EXCLUDE_GPS previously sent GPS coordinates to the webhook."""
        monkeypatch.setenv("EXCLUDE_GPS", value)
        with pytest.raises(ConfigError, match="EXCLUDE_GPS must be one of"):
            load_config(empty_env_file, cli_dry_run=True)


class TestLogLevelValidation:
    @pytest.mark.parametrize(
        "value", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "debug", " info "]
    )
    def test_accepted_levels(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", value)
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.log_level == value.strip().upper()

    @pytest.mark.parametrize("value", ["VERBOSE", "warn", "trace", "notset"])
    def test_unknown_level_raises(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        """Previously resolved silently to INFO, or crashed uvicorn with a KeyError."""
        monkeypatch.setenv("LOG_LEVEL", value)
        with pytest.raises(ConfigError, match="LOG_LEVEL must be one of"):
            load_config(empty_env_file, cli_dry_run=True)

    def test_every_accepted_level_is_valid_for_uvicorn(self) -> None:
        """server.py passes log_level.lower() straight into uvicorn.run()."""
        from uvicorn.config import LOG_LEVELS

        from fit_pipeline.config import _LOG_LEVELS

        for level in _LOG_LEVELS:
            assert level.lower() in LOG_LEVELS

    def test_every_accepted_level_resolves_on_logging_module(self) -> None:
        """configure_logging does getattr(logging, level)."""
        import logging

        from fit_pipeline.config import _LOG_LEVELS

        for level in _LOG_LEVELS:
            assert isinstance(getattr(logging, level, None), int)


class TestEnvFileWithUnfilledKeys:
    def test_env_example_style_blank_keys_load_cleanly(
        self, clean_env: None, tmp_path: Path
    ) -> None:
        """The shape .env.example produces must load without error."""
        env_file = tmp_path / "unfilled.env"
        env_file.write_text(
            "WEBHOOK_DESTINATIONS="
            '[{"url": "https://a.example.com/h", "secret": "sk_a"}]\n'
            "THRESHOLD_HR=\n"
            "HR_ZONE_1=\n"
            "PACE_ZONE_EASY=\n"
            "THRESHOLD_PACE=\n"
            "RESTING_HR=\n"
            "MAX_HR=\n"
            "OUTPUT_FILE=\n"
            "LOG_FILE=\n"
            "EXCLUDE_FIELDS=\n"
            "STREAM_SAMPLE_RATE=\n"
        )

        config = load_config(str(env_file))

        assert config.threshold_hr is None
        assert config.hr_zone_1 is None
        assert config.pace_zone_easy is None
        assert config.threshold_pace is None
        assert config.resting_hr is None
        assert config.max_hr is None
        assert config.stream_sample_rate == 3
        assert config.exclude_fields == []
        # Blank OUTPUT_FILE must not divert delivery away from the webhook.
        assert config.output_file == ""
        assert config.log_file == ""
        assert len(config.webhook_destinations) == 1

    def test_whitespace_output_file_does_not_relax_validation(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A padded OUTPUT_FILE is the same silent webhook diversion as a blank one."""
        monkeypatch.setenv("OUTPUT_FILE", "   ")
        with pytest.raises(ConfigError, match="WEBHOOK_DESTINATIONS"):
            load_config(empty_env_file)

    def test_whitespace_string_fields_are_empty(
        self, clean_env: None, empty_env_file: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SERVER_SECRET", "  ")
        monkeypatch.setenv("UPLOAD_DIR", "  ")
        monkeypatch.setenv("LOG_FILE", "  ")
        monkeypatch.setenv("LOG_LEVEL", "  ")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.server_secret == ""
        assert config.upload_dir == ""
        assert config.log_file == ""
        assert config.log_level == "INFO"


class TestEnvExampleFile:
    """Guard the shipped .env.example against the inline-comment trap directly."""

    @staticmethod
    def _example_values() -> dict[str, str | None]:
        from dotenv import dotenv_values

        example = Path(__file__).resolve().parent.parent / ".env.example"
        assert example.is_file(), f"missing {example}"
        return dict(dotenv_values(example))

    def test_no_value_is_a_stray_inline_comment(self) -> None:
        """`KEY=  # comment` assigns the comment text — never ship that shape."""
        offenders = {
            key: value
            for key, value in self._example_values().items()
            if value and value.lstrip().startswith("#")
        }
        assert not offenders, (
            "These .env.example keys have an inline comment as their value; "
            f"move the comment to its own line: {sorted(offenders)}"
        )

    INT_KEYS = (
        "SERVER_PORT",
        "STREAM_SAMPLE_RATE",
        "THRESHOLD_HR",
        "HR_ZONE_1",
        "HR_ZONE_2",
        "HR_ZONE_3",
        "HR_ZONE_4",
        "HR_ZONE_5",
        "PACE_ZONE_EASY",
        "PACE_ZONE_MODERATE",
        "PACE_ZONE_THRESHOLD",
        "THRESHOLD_PACE",
        "RESTING_HR",
        "MAX_HR",
    )

    def test_int_typed_keys_are_blank_or_parseable(self) -> None:
        values = self._example_values()
        for key in self.INT_KEYS:
            value = (values.get(key) or "").strip()
            if value:
                int(value)  # raises if .env.example ships an unparseable default

    def test_loads_without_error_in_dry_run(self, clean_env: None) -> None:
        example = Path(__file__).resolve().parent.parent / ".env.example"
        config = load_config(str(example), cli_dry_run=True)
        assert config.stream_sample_rate > 0
        assert config.trimp_gender in ("male", "female")


class TestCompletedFilenameFormat:
    """Bad patterns must fail at startup, not part-way through a batch."""

    def test_unset_defaults_to_empty(
        self, clean_env: None, empty_env_file: str
    ) -> None:
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.completed_filename_format == ""

    def test_valid_pattern_accepted(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("COMPLETED_FILENAME_FORMAT", "%Y-%m-%d-%H%M")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.completed_filename_format == "%Y-%m-%d-%H%M"

    def test_path_separator_raises(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("COMPLETED_FILENAME_FORMAT", "%Y/%m/%d")
        with pytest.raises(ConfigError, match="path separators"):
            load_config(empty_env_file, cli_dry_run=True)

    def test_blank_pattern_treated_as_unset(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("COMPLETED_FILENAME_FORMAT", "   ")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.completed_filename_format == ""

    def test_pattern_producing_empty_name_raises(self) -> None:
        # load_config strips blank values, so this guard is reached only by a
        # Config built in code.
        config = Config(dry_run=True, completed_filename_format="   ")
        with pytest.raises(ConfigError, match="non-empty filename"):
            _validate(config)

    def test_set_mtime_defaults_to_false(
        self, clean_env: None, empty_env_file: str
    ) -> None:
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.completed_set_mtime is False

    def test_set_mtime_enabled(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("COMPLETED_SET_MTIME", "true")
        config = load_config(empty_env_file, cli_dry_run=True)
        assert config.completed_set_mtime is True

    def test_set_mtime_rejects_typo(
        self,
        clean_env: None,
        empty_env_file: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("COMPLETED_SET_MTIME", "yep")
        with pytest.raises(ConfigError, match="COMPLETED_SET_MTIME"):
            load_config(empty_env_file, cli_dry_run=True)

    def test_separator_rejected_by_validate(self) -> None:
        config = Config(dry_run=True, completed_filename_format="%Y/%m")
        with pytest.raises(ConfigError, match="path separators"):
            _validate(config)
