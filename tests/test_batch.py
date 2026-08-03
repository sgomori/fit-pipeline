"""Tests for fit_pipeline.batch."""

import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from fit_pipeline.batch import _completed_filename, process_directory
from fit_pipeline.config import Config
from fit_pipeline.core import SCHEMA_VERSION
from fit_pipeline.exceptions import DeliveryError, ParseError
from tests.conftest import SAMPLE_FIT

DATE_FORMAT = "%Y-%m-%d-%H%M"


def _payload(started_at_local: str | None, started_at: str | None = None) -> dict:
    """Minimal delivered payload carrying only the activity start times."""
    activity = {}
    if started_at_local is not None:
        activity["started_at_local"] = started_at_local
    if started_at is not None:
        activity["started_at"] = started_at
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

    def test_already_prefixed_name_is_not_prefixed_again(
        self, base_config: Config
    ) -> None:
        # Reprocessing a completed file must not stack a second date onto it.
        base_config.completed_filename_format = DATE_FORMAT
        name = _completed_filename(
            Path("2026-07-25-1136_463372454903.fit"),
            base_config,
            _payload("2026-07-25T11:36:04"),
        )
        assert name == "2026-07-25-1136_463372454903.fit"

    def test_renaming_is_idempotent(self, base_config: Config) -> None:
        base_config.completed_filename_format = DATE_FORMAT
        payload = _payload("2026-07-25T11:36:04")

        once = _completed_filename(Path("463372454903.fit"), base_config, payload)
        twice = _completed_filename(Path(once), base_config, payload)

        assert twice == once

    def test_prefix_from_a_different_date_is_still_prefixed(
        self, base_config: Config
    ) -> None:
        # Only this activity's own prefix is recognised. A stem carrying some
        # other date is a genuinely different name, and silently stripping it
        # would be a worse failure than double-prefixing.
        base_config.completed_filename_format = DATE_FORMAT
        name = _completed_filename(
            Path("2020-01-01-0700_463372454903.fit"),
            base_config,
            _payload("2026-07-25T11:36:04"),
        )
        assert name == "2026-07-25-1136_2020-01-01-0700_463372454903.fit"

    def test_prefix_in_a_different_format_is_still_prefixed(
        self, base_config: Config
    ) -> None:
        # The guard compares against the rendered pattern, not a parsed date,
        # so a stem written by some other format is not treated as a prefix.
        base_config.completed_filename_format = DATE_FORMAT
        name = _completed_filename(
            Path("20260725_463372454903.fit"),
            base_config,
            _payload("2026-07-25T11:36:04"),
        )
        assert name == "2026-07-25-1136_20260725_463372454903.fit"


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

    def test_reprocessing_a_renamed_file_does_not_duplicate_it(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        # A completed file fed back in — re-uploaded, or copied back to the
        # watch directory. Double-prefixing would produce a name matching
        # nothing in completed/, slipping past the no-overwrite guard and
        # leaving two files for one activity.
        _copy_fixture_to(tmp_path, "2026-07-25-1136_463372454903.fit")
        base_config.completed_filename_format = DATE_FORMAT

        with patch(
            "fit_pipeline.batch.process_file",
            return_value=_payload("2026-07-25T11:36:04"),
        ):
            process_directory(tmp_path, [], base_config)

        completed = tmp_path / "completed"
        assert [p.name for p in completed.iterdir()] == [
            "2026-07-25-1136_463372454903.fit"
        ]


class TestCompletedMtime:
    """Rewriting mtime is opt-in and never guesses an instant it cannot derive."""

    UTC_START = "2026-07-25T18:36:04+00:00"

    def _run(self, tmp_path: Path, config: Config, payload: dict) -> Path:
        _copy_fixture_to(tmp_path, "463372454903.fit")
        with patch("fit_pipeline.batch.process_file", return_value=payload):
            process_directory(tmp_path, [], config)
        return tmp_path / "completed" / "463372454903.fit"

    def test_default_leaves_mtime_untouched(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        before = time.time()
        dest = self._run(tmp_path, base_config, _payload(None, self.UTC_START))
        # The copy's mtime is "now", not the 2026-07-25 activity instant.
        assert dest.stat().st_mtime >= before - 5

    def test_mtime_set_to_activity_start(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        base_config.completed_set_mtime = True
        dest = self._run(tmp_path, base_config, _payload(None, self.UTC_START))

        expected = datetime.fromisoformat(self.UTC_START).timestamp()
        assert dest.stat().st_mtime == pytest.approx(expected, abs=1)

    def test_mtime_is_the_utc_instant_not_the_local_clock(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        # started_at_local is 11:36 while the instant is 18:36 UTC. An mtime is
        # a point in time, so the local field must not be the source.
        base_config.completed_set_mtime = True
        dest = self._run(
            tmp_path, base_config, _payload("2026-07-25T11:36:04", self.UTC_START)
        )

        stored = datetime.fromtimestamp(dest.stat().st_mtime, tz=timezone.utc)
        assert stored.hour == 18

    def test_renaming_and_mtime_combine(
        self, tmp_path: Path, base_config: Config
    ) -> None:
        base_config.completed_filename_format = DATE_FORMAT
        base_config.completed_set_mtime = True
        _copy_fixture_to(tmp_path, "463372454903.fit")

        with patch(
            "fit_pipeline.batch.process_file",
            return_value=_payload("2026-07-25T11:36:04", self.UTC_START),
        ):
            process_directory(tmp_path, [], base_config)

        renamed = tmp_path / "completed" / "2026-07-25-1136_463372454903.fit"
        assert renamed.exists()
        expected = datetime.fromisoformat(self.UTC_START).timestamp()
        assert renamed.stat().st_mtime == pytest.approx(expected, abs=1)

    def test_missing_start_time_leaves_mtime_untouched(
        self, tmp_path: Path, base_config: Config, caplog
    ) -> None:
        base_config.completed_set_mtime = True
        before = time.time()
        dest = self._run(tmp_path, base_config, _payload(None))

        assert dest.stat().st_mtime >= before - 5
        assert "no start time" in caplog.text

    def test_unparseable_start_time_leaves_mtime_untouched(
        self, tmp_path: Path, base_config: Config, caplog
    ) -> None:
        base_config.completed_set_mtime = True
        before = time.time()
        dest = self._run(tmp_path, base_config, _payload(None, "not-a-date"))

        assert dest.stat().st_mtime >= before - 5
        assert "unparseable start time" in caplog.text

    def test_naive_start_time_leaves_mtime_untouched(
        self, tmp_path: Path, base_config: Config, caplog
    ) -> None:
        # Without an offset there is no absolute instant, and no home timezone
        # is assumed — the same rule the renaming path follows.
        base_config.completed_set_mtime = True
        before = time.time()
        dest = self._run(tmp_path, base_config, _payload(None, "2026-07-25T18:36:04"))

        assert dest.stat().st_mtime >= before - 5
        assert "without a UTC offset" in caplog.text
