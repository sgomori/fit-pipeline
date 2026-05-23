"""Tests for fit_pipeline.batch."""

import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fit_pipeline.batch import process_directory
from fit_pipeline.config import Config
from fit_pipeline.exceptions import DeliveryError, ParseError
from tests.conftest import SAMPLE_FIT


def _copy_fixture_to(dest_dir: Path, name: str = "run.fit") -> Path:
    """Copy the sample FIT fixture to dest_dir with a given name."""
    if not SAMPLE_FIT.exists():
        pytest.skip("Sample FIT fixture not yet available")
    dest = dest_dir / name
    shutil.copy(SAMPLE_FIT, dest)
    return dest


class TestProcessDirectoryErrors:
    def test_raises_on_non_directory(self, base_config: Config) -> None:
        with pytest.raises(ValueError, match="Not a directory"):
            process_directory("/nonexistent/path/", [], base_config)

    def test_raises_on_empty_directory(self, tmp_path: Path, base_config: Config) -> None:
        with pytest.raises(ValueError, match="No .fit files"):
            process_directory(tmp_path, [], base_config)

    def test_ignores_non_fit_files(self, tmp_path: Path, base_config: Config) -> None:
        (tmp_path / "readme.txt").write_text("not a fit file")
        with pytest.raises(ValueError, match="No .fit files"):
            process_directory(tmp_path, [], base_config)


class TestBatchWithFitFixture:
    """Batch tests that require the real FIT fixture."""

    def test_processes_single_file_and_moves_to_completed(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        fit = _copy_fixture_to(tmp_path, "activity.fit")
        results = process_directory(tmp_path, [], base_config)

        assert len(results) == 1
        assert not fit.exists(), "Original file should have been moved"
        assert (tmp_path / "completed" / "activity.fit").exists()

    def test_completed_directory_created(self, tmp_path: Path, base_config: Config) -> None:
        _copy_fixture_to(tmp_path, "run.fit")
        process_directory(tmp_path, [], base_config)
        assert (tmp_path / "completed").is_dir()

    def test_processes_multiple_files_in_order(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")

        # Copy fixture twice with distinct names and mod times
        _copy_fixture_to(tmp_path, "run_a.fit")
        time.sleep(0.01)
        _copy_fixture_to(tmp_path, "run_b.fit")

        results = process_directory(tmp_path, [], base_config)

        assert len(results) == 2
        assert results[0]["file"] == "run_a.fit"
        assert results[1]["file"] == "run_b.fit"

    def test_payload_has_schema_version(self, tmp_path: Path, base_config: Config) -> None:
        _copy_fixture_to(tmp_path)
        results = process_directory(tmp_path, [], base_config)
        assert results[0]["schema_version"] == "1.0"

    def test_already_completed_files_not_reprocessed(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        _copy_fixture_to(tmp_path, "run.fit")
        completed_dir = tmp_path / "completed"
        completed_dir.mkdir()
        already_done = completed_dir / "old_run.fit"
        shutil.copy(SAMPLE_FIT, already_done)

        results = process_directory(tmp_path, [], base_config)

        # Only the one file in source dir was processed
        assert len(results) == 1
        assert (tmp_path / "completed" / "old_run.fit").exists(), "Old file untouched"

    def test_failed_file_remains_in_source(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        fit = _copy_fixture_to(tmp_path, "bad.fit")

        with patch(
            "fit_pipeline.batch.process_file",
            side_effect=ParseError("synthetic parse failure"),
        ), pytest.raises(ParseError):
            process_directory(tmp_path, [], base_config)

        assert fit.exists(), "Failed file must remain in source directory"
        assert not (tmp_path / "completed").exists() or not (
            tmp_path / "completed" / "bad.fit"
        ).exists()

    def test_batch_halts_on_first_failure(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        if not SAMPLE_FIT.exists():
            pytest.skip("Sample FIT fixture not yet available")

        _copy_fixture_to(tmp_path, "run_a.fit")
        time.sleep(0.01)
        _copy_fixture_to(tmp_path, "run_b.fit")

        call_count = 0

        def failing_process(path, processors, config):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise DeliveryError("first file failed")
            return {}

        with patch("fit_pipeline.batch.process_file", side_effect=failing_process), pytest.raises(DeliveryError):
            process_directory(tmp_path, [], base_config)

        assert call_count == 1, "Batch must halt after first failure"
