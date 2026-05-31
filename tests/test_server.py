"""Tests for the FastAPI HTTP endpoint (fit_pipeline/server.py).

Uses FastAPI TestClient — no real HTTP requests are made.
"""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from fit_pipeline.config import Config
from fit_pipeline.exceptions import DeliveryError, ParseError
from fit_pipeline.server import create_app
from tests.conftest import SAMPLE_FIT

_SERVER_SECRET = "test_server_secret"
_AUTH_HEADERS = {"Authorization": f"Bearer {_SERVER_SECRET}"}


@pytest.fixture
def server_config(base_config: Config) -> Config:
    base_config.server_secret = _SERVER_SECRET
    base_config.dry_run = True
    return base_config


@pytest.fixture
def client(server_config: Config) -> TestClient:
    application = create_app(server_config, [])
    return TestClient(application, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_missing_token_returns_401(self, client: TestClient) -> None:
        response = client.post("/process", json={"path": "/some/path"})
        assert response.status_code == 401

    def test_wrong_token_returns_401(self, client: TestClient) -> None:
        response = client.post(
            "/process",
            json={"path": "/some/path"},
            headers={"Authorization": "Bearer wrong_token"},
        )
        assert response.status_code == 401

    def test_correct_token_passes_authentication(self, client: TestClient, tmp_path: Path) -> None:
        # Path doesn't need to be valid for auth test — we just need auth to pass
        response = client.post(
            "/process",
            json={"path": str(tmp_path / "nonexistent.fit")},
            headers=_AUTH_HEADERS,
        )
        # 422 (path not found) — not 401
        assert response.status_code != 401


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

class TestRequestValidation:
    def test_missing_path_field_returns_422(self, client: TestClient) -> None:
        response = client.post("/process", json={}, headers=_AUTH_HEADERS)
        assert response.status_code == 422

    def test_nonexistent_path_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/process",
            json={"path": "/definitely/does/not/exist.fit"},
            headers=_AUTH_HEADERS,
        )
        assert response.status_code == 422
        body = response.json()
        assert "not found" in body.get("detail", "").lower()

    def test_non_fit_file_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")
        response = client.post(
            "/process",
            json={"path": str(txt)},
            headers=_AUTH_HEADERS,
        )
        assert response.status_code == 422

    def test_empty_directory_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        response = client.post(
            "/process",
            json={"path": str(tmp_path)},
            headers=_AUTH_HEADERS,
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Successful processing (requires FIT fixture)
# ---------------------------------------------------------------------------

class TestSuccessfulProcessing:
    def test_single_file_returns_200_with_ok_status(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")
        fit = tmp_path / "run.fit"
        shutil.copy(SAMPLE_FIT, fit)

        response = client.post(
            "/process", json={"path": str(fit)}, headers=_AUTH_HEADERS
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["processed"] == 1
        assert body["failed"] == 0
        assert len(body["files"]) == 1

    def test_directory_returns_200_with_correct_counts(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")
        shutil.copy(SAMPLE_FIT, tmp_path / "run_a.fit")
        shutil.copy(SAMPLE_FIT, tmp_path / "run_b.fit")

        response = client.post(
            "/process", json={"path": str(tmp_path)}, headers=_AUTH_HEADERS
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["processed"] == 2

    def test_response_has_consistent_shape(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")
        shutil.copy(SAMPLE_FIT, tmp_path / "run.fit")

        response = client.post(
            "/process", json={"path": str(tmp_path)}, headers=_AUTH_HEADERS
        )
        body = response.json()
        required_keys = {"status", "processed", "failed", "files"}
        assert required_keys.issubset(body.keys())
        for file_entry in body["files"]:
            assert "file" in file_entry
            assert "status" in file_entry


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_parse_error_returns_500_with_structured_json(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")
        shutil.copy(SAMPLE_FIT, tmp_path / "run.fit")

        with patch(
            "fit_pipeline.server.process_file",
            side_effect=ParseError("synthetic parse error"),
        ):
            response = client.post(
                "/process", json={"path": str(tmp_path / "run.fit")}, headers=_AUTH_HEADERS
            )
        assert response.status_code == 500
        body = response.json()
        assert body["status"] == "error"
        # No raw traceback in response
        assert "Traceback" not in json.dumps(body)

    def test_delivery_error_returns_500(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")
        shutil.copy(SAMPLE_FIT, tmp_path / "run.fit")

        with patch(
            "fit_pipeline.server.process_file",
            side_effect=DeliveryError("webhook unreachable"),
        ):
            response = client.post(
                "/process", json={"path": str(tmp_path / "run.fit")}, headers=_AUTH_HEADERS
            )
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_returns_200(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------------

class TestUploadEndpoint:
    @pytest.fixture
    def upload_config(self, tmp_path: Path, base_config: Config) -> Config:
        base_config.server_secret = _SERVER_SECRET
        base_config.dry_run = True
        base_config.upload_dir = str(tmp_path / "uploads")
        return base_config

    @pytest.fixture
    def upload_client(self, upload_config: Config) -> TestClient:
        application = create_app(upload_config, [])
        return TestClient(application, raise_server_exceptions=False)

    def test_missing_auth_returns_401(self, upload_client: TestClient) -> None:
        response = upload_client.post("/upload", files={"file": ("run.fit", b"data", "application/octet-stream")})
        assert response.status_code == 401

    def test_wrong_token_returns_401(self, upload_client: TestClient) -> None:
        response = upload_client.post(
            "/upload",
            files={"file": ("run.fit", b"data", "application/octet-stream")},
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401

    def test_non_fit_file_returns_422(self, upload_client: TestClient) -> None:
        response = upload_client.post(
            "/upload",
            files={"file": ("readme.txt", b"hello", "text/plain")},
            headers=_AUTH_HEADERS,
        )
        assert response.status_code == 422

    def test_unconfigured_upload_dir_returns_503(self, base_config: Config) -> None:
        base_config.server_secret = _SERVER_SECRET
        base_config.dry_run = True
        base_config.upload_dir = ""
        app_no_dir = create_app(base_config, [])
        client_no_dir = TestClient(app_no_dir, raise_server_exceptions=False)
        response = client_no_dir.post(
            "/upload",
            files={"file": ("run.fit", b"data", "application/octet-stream")},
            headers=_AUTH_HEADERS,
        )
        assert response.status_code == 503

    def test_valid_fit_returns_200(self, upload_client: TestClient, tmp_path: Path) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")
        fit_bytes = SAMPLE_FIT.read_bytes()
        response = upload_client.post(
            "/upload",
            files={"file": ("run.fit", fit_bytes, "application/octet-stream")},
            headers=_AUTH_HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["processed"] == 1
        assert body["failed"] == 0
        assert len(body["files"]) == 1
        assert body["files"][0]["status"] == "ok"

    def test_file_moved_to_completed_on_success(self, upload_config: Config) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")
        application = create_app(upload_config, [])
        client = TestClient(application, raise_server_exceptions=False)
        fit_bytes = SAMPLE_FIT.read_bytes()
        client.post(
            "/upload",
            files={"file": ("run.fit", fit_bytes, "application/octet-stream")},
            headers=_AUTH_HEADERS,
        )
        completed = Path(upload_config.upload_dir) / "completed" / "run.fit"
        assert completed.exists()

    def test_process_error_returns_500(self, upload_client: TestClient) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")
        fit_bytes = SAMPLE_FIT.read_bytes()
        with patch(
            "fit_pipeline.server.process_file",
            side_effect=ParseError("synthetic parse error"),
        ):
            response = upload_client.post(
                "/upload",
                files={"file": ("run.fit", fit_bytes, "application/octet-stream")},
                headers=_AUTH_HEADERS,
            )
        assert response.status_code == 500
        body = response.json()
        assert body["status"] == "error"
        assert body["failed"] == 1

    def test_path_traversal_filename_is_sanitized(self, upload_config: Config) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")
        application = create_app(upload_config, [])
        client = TestClient(application, raise_server_exceptions=False)
        fit_bytes = SAMPLE_FIT.read_bytes()
        response = client.post(
            "/upload",
            files={"file": ("../../evil.fit", fit_bytes, "application/octet-stream")},
            headers=_AUTH_HEADERS,
        )
        assert response.status_code == 200
        upload_dir = Path(upload_config.upload_dir)
        # File landed inside upload_dir/completed as a basename, not outside it
        assert (upload_dir / "completed" / "evil.fit").exists()
        assert not (upload_dir.parent / "evil.fit").exists()
