"""Tests for fit_pipeline.batch."""

import shutil
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fit_pipeline.batch import _completed_filename, process_directory
from fit_pipeline.config import Config
from fit_pipeline.core import SCHEMA_VERSION
from fit_pipeline.exceptions import DeliveryError, ParseError
from tests.conftest import SAMPLE_FIT

DATE_FORMAT = "%Y-%m-%d-%H%M"


def _payload(started_at_local: str | None) -> dict:
    """Minimal delivered payload carrying only the local start time."""
    activity = {} if started_at_local is None else {"started_at_local": started_at_local}
    return {"schema_version": SCHEMA_VERSION, "activity": activity}


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
        assert results[0]["schema_version"] == SCHEMA_VERSION

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


class TestCompletedFilename:
    """Renaming is opt-in and must never invent a date it cannot verify."""

    def test_original_name_kept_when_format_unset(self, base_config: Config) -> None:
        name = _completed_filename(
            Path("463372454903.fit"), base_config, _payload("2026-07-25T11:36:04")
        )
        assert name == "463372454903.fit"

    def test_prefixes_local_date_and_preserves_stem(self, base_config: Config) -> None:
        base_config.completed_filename_format = DATE_FORMAT
        name = _completed_filename(
            Path("463372454903.fit"), base_config, _payload("2026-07-25T11:36:04")
        )
        assert name == "2026-07-25-1136_463372454903.fit"

    def test_uses_local_date_not_utc_date(self, base_config: Config) -> None:
        # 17:08 local on the 29th is 00:08 UTC on the 30th. Naming from UTC
        # would misdate this activity, which is the failure mode for ~23% of
        # evening activities.
        base_config.completed_filename_format = DATE_FORMAT
        name = _completed_filename(
            Path("act.fit"), base_config, _payload("2026-05-29T17:08:11")
        )
        assert name.startswith("2026-05-29-1708_")

    def test_keeps_original_name_when_local_time_missing(
        self, base_config: Config, caplog
    ) -> None:
        base_config.completed_filename_format = DATE_FORMAT
        name = _completed_filename(Path("act.fit"), base_config, _payload(None))
        assert name == "act.fit"
        assert "no local start time" in caplog.text

    def test_keeps_original_name_when_payload_missing(self, base_config: Config) -> None:
        base_config.completed_filename_format = DATE_FORMAT
        assert _completed_filename(Path("act.fit"), base_config, None) == "act.fit"

    def test_keeps_original_name_when_local_time_unparseable(
        self, base_config: Config, caplog
    ) -> None:
        base_config.completed_filename_format = DATE_FORMAT
        name = _completed_filename(Path("act.fit"), base_config, _payload("not-a-date"))
        assert name == "act.fit"
        assert "unparseable local start time" in caplog.text


class TestBatchRenaming:
    """End-to-end renaming through process_directory."""

    def test_file_renamed_in_completed(self, tmp_path: Path, base_config: Config) -> None:
        _copy_fixture_to(tmp_path, "463372454903.fit")
        base_config.completed_filename_format = DATE_FORMAT

        with patch(
            "fit_pipeline.batch.process_file",
            return_value=_payload("2026-07-25T11:36:04"),
        ):
            process_directory(tmp_path, [], base_config)

        assert (tmp_path / "completed" / "2026-07-25-1136_463372454903.fit").exists()
        assert not (tmp_path / "completed" / "463372454903.fit").exists()

    def test_default_leaves_filename_untouched(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        _copy_fixture_to(tmp_path, "463372454903.fit")
        process_directory(tmp_path, [], base_config)
        assert (tmp_path / "completed" / "463372454903.fit").exists()

    def test_existing_target_is_not_overwritten(
        self, tmp_path: Path, base_config: Config, caplog
    ) -> None:
        _copy_fixture_to(tmp_path, "463372454903.fit")
        base_config.completed_filename_format = DATE_FORMAT

        completed = tmp_path / "completed"
        completed.mkdir()
        clash = completed / "2026-07-25-1136_463372454903.fit"
        clash.write_bytes(b"previously completed activity")

        with patch(
            "fit_pipeline.batch.process_file",
            return_value=_payload("2026-07-25T11:36:04"),
        ):
            process_directory(tmp_path, [], base_config)

        assert clash.read_bytes() == b"previously completed activity"
        assert (completed / "463372454903.fit").exists()
        assert "already exists" in caplog.text
