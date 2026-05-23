"""Tests for fit_pipeline.delivery."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fit_pipeline.config import Config
from fit_pipeline.delivery import (
    DryRunDelivery,
    FileDelivery,
    WebhookDelivery,
    make_delivery,
)
from fit_pipeline.exceptions import DeliveryError

_SAMPLE_PAYLOAD = {
    "schema_version": "1.0",
    "source": "garmin_fit",
    "file": "test.fit",
    "processed_at": "2024-03-15T09:00:00+00:00",
    "activity": {"distance_meters": 10000},
}


class TestWebhookDelivery:
    def _mock_response(self, status_code: int, text: str = "") -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        return resp

    def test_delivers_on_200(self) -> None:
        delivery = WebhookDelivery("https://example.com/webhook", "secret")
        with patch("httpx.post", return_value=self._mock_response(200)) as mock_post:
            delivery.deliver(_SAMPLE_PAYLOAD)
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            assert kwargs["headers"]["Authorization"] == "Bearer secret"
            body = json.loads(mock_post.call_args[1]["content"])
            assert body["schema_version"] == "1.0"

    def test_retries_on_non_200_then_raises(self) -> None:
        delivery = WebhookDelivery("https://example.com/webhook", "secret")
        with patch("httpx.post", return_value=self._mock_response(500, "server error")), patch("time.sleep"), pytest.raises(DeliveryError):
            delivery.deliver(_SAMPLE_PAYLOAD)

    def test_retries_exactly_twice(self) -> None:
        delivery = WebhookDelivery("https://example.com/webhook", "secret")
        with patch("httpx.post", return_value=self._mock_response(503)) as mock_post:
            with patch("time.sleep"), pytest.raises(DeliveryError):
                delivery.deliver(_SAMPLE_PAYLOAD)
            assert mock_post.call_count == 2

    def test_succeeds_on_retry_after_initial_failure(self) -> None:
        delivery = WebhookDelivery("https://example.com/webhook", "secret")
        responses = [self._mock_response(500), self._mock_response(200)]
        with patch("httpx.post", side_effect=responses), patch("time.sleep"):
            delivery.deliver(_SAMPLE_PAYLOAD)  # should not raise

    def test_raises_on_connection_error_after_retry(self) -> None:
        import httpx as httpx_module
        delivery = WebhookDelivery("https://example.com/webhook", "secret")
        with patch("httpx.post", side_effect=httpx_module.ConnectError("refused")), patch("time.sleep"), pytest.raises(DeliveryError):
            delivery.deliver(_SAMPLE_PAYLOAD)

    def test_bearer_token_in_header(self) -> None:
        delivery = WebhookDelivery("https://example.com/webhook", "my_token")
        with patch("httpx.post", return_value=self._mock_response(200)) as mock_post:
            delivery.deliver(_SAMPLE_PAYLOAD)
            headers = mock_post.call_args[1]["headers"]
            assert headers["Authorization"] == "Bearer my_token"


class TestFileDelivery:
    def test_writes_json_to_file(self, tmp_path: Path) -> None:
        output = tmp_path / "output.json"
        delivery = FileDelivery(output)
        delivery.deliver(_SAMPLE_PAYLOAD)
        assert output.exists()
        written = json.loads(output.read_text())
        assert written["schema_version"] == "1.0"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        output = tmp_path / "subdir" / "deep" / "output.json"
        delivery = FileDelivery(output)
        delivery.deliver(_SAMPLE_PAYLOAD)
        assert output.exists()

    def test_raises_delivery_error_on_write_failure(self, tmp_path: Path) -> None:
        output = tmp_path / "output.json"
        delivery = FileDelivery(output)
        with patch.object(Path, "write_text", side_effect=OSError("disk full")), pytest.raises(DeliveryError, match="disk full"):
            delivery.deliver(_SAMPLE_PAYLOAD)


class TestDryRunDelivery:
    def test_prints_to_stdout(self, capsys) -> None:
        delivery = DryRunDelivery()
        delivery.deliver(_SAMPLE_PAYLOAD)
        captured = capsys.readouterr()
        written = json.loads(captured.out)
        assert written["schema_version"] == "1.0"

    def test_does_not_make_http_requests(self) -> None:
        delivery = DryRunDelivery()
        with patch("httpx.post") as mock_post:
            delivery.deliver(_SAMPLE_PAYLOAD)
            mock_post.assert_not_called()


class TestMakeDelivery:
    def test_returns_dry_run_when_configured(self, base_config: Config) -> None:
        base_config.dry_run = True
        assert isinstance(make_delivery(base_config), DryRunDelivery)

    def test_returns_file_when_output_file_configured(self, base_config: Config) -> None:
        base_config.dry_run = False
        base_config.output_file = "/tmp/out.json"
        assert isinstance(make_delivery(base_config), FileDelivery)

    def test_returns_webhook_when_url_configured(self, base_config: Config) -> None:
        base_config.dry_run = False
        base_config.output_file = ""
        base_config.webhook_url = "https://example.com/hook"
        base_config.webhook_secret = "secret"
        assert isinstance(make_delivery(base_config), WebhookDelivery)

    def test_schema_version_present_in_delivered_payload(self, tmp_path: Path, base_config: Config) -> None:
        output = tmp_path / "out.json"
        base_config.output_file = str(output)
        base_config.dry_run = False
        delivery = make_delivery(base_config)
        delivery.deliver(_SAMPLE_PAYLOAD)
        written = json.loads(output.read_text())
        assert "schema_version" in written
        assert written["schema_version"] == "1.0"
