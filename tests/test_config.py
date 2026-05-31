"""Tests for fit_pipeline.config — WEBHOOK_DESTINATIONS parsing and validation."""

import pytest

from fit_pipeline.config import (
    Config,
    WebhookDestination,
    _validate,
    _webhook_destinations,
)
from fit_pipeline.exceptions import ConfigError


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
