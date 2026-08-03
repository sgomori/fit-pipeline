"""Batch processing — iterate a directory of FIT files safely.

Completed files are moved to a completed/ subdirectory before the next file
is attempted, making the batch safe to restart after interruption.
"""

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fit_pipeline.config import Config
from fit_pipeline.core import process_file
from fit_pipeline.processor import Processor

logger = logging.getLogger(__name__)

_COMPLETED_DIRNAME = "completed"


def process_directory(
    directory: str | Path,
    processors: list[Processor],
    config: Config,
) -> list[dict[str, Any]]:
    """Process all .fit files in a directory, in chronological order.

    Files are sorted by modification time (oldest first). Each file is fully
    processed and delivered before the next begins. Successfully processed
    files are moved to a ``completed/`` subdirectory. A failed file halts
    the batch — subsequent files are not attempted.

    Args:
        directory: Path to the source directory.
        processors: Instantiated processor chain.
        config: Loaded pipeline configuration.

    Returns:
        List of delivered payload dicts for successfully processed files.

    Raises:
        ValueError: If the path is not a directory or contains no FIT files.
        ParseError: If a FIT file is malformed.
        MiddlewareError: If a processor raises an exception.
        DeliveryError: If delivery fails after retry.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError(f"Not a directory: {directory}")

    fit_files = sorted(
        [f for f in directory.iterdir() if f.suffix.lower() == ".fit" and f.is_file()],
        key=lambda f: f.stat().st_mtime,
    )

    if not fit_files:
        raise ValueError(f"No .fit files found in: {directory}")

    logger.info(
        "Batch: found %d FIT file(s) in %s", len(fit_files), directory.name
    )

    completed_dir = directory / _COMPLETED_DIRNAME
    results: list[dict[str, Any]] = []

    for i, fit_path in enumerate(fit_files, start=1):
        logger.info("[%d/%d] %s", i, len(fit_files), fit_path.name)

        payload = process_file(fit_path, processors, config)
        results.append(payload)

        move_to_completed(fit_path, completed_dir, config, payload)

    logger.info(
        "Batch complete: %d/%d files processed", len(results), len(fit_files)
    )
    return results


def _completed_filename(
    fit_path: Path,
    config: Config,
    payload: dict[str, Any] | None,
) -> str:
    """Determine the name a processed file should take in completed/.

    Renaming is opt-in via ``COMPLETED_FILENAME_FORMAT``. The original stem is
    always appended, which keeps the source activity ID recoverable and makes
    name collisions all but impossible.

    The date comes from the activity's *local* start time. A UTC date would put
    roughly a quarter of evening activities on the following day, so when local
    time is unavailable the received name is kept rather than risk a wrong date.

    Renaming is idempotent: a file already carrying its own start-date prefix is
    left alone, so reprocessing one cannot stack a second date onto the name.

    Args:
        fit_path: Path to the processed FIT file.
        config: Loaded pipeline configuration.
        payload: Delivered payload, or None when unavailable.

    Returns:
        The filename to use — the original name when no rename applies.
    """
    if not config.completed_filename_format:
        return fit_path.name

    started_local = (payload or {}).get("activity", {}).get("started_at_local")
    if not started_local:
        logger.warning(
            "%s has no local start time; keeping the original filename. "
            "The FIT file is missing a usable local_timestamp.",
            fit_path.name,
        )
        return fit_path.name

    try:
        parsed = datetime.fromisoformat(started_local)
    except ValueError:
        logger.warning(
            "%s has an unparseable local start time %r; keeping the original filename",
            fit_path.name,
            started_local,
        )
        return fit_path.name

    prefix = parsed.strftime(config.completed_filename_format)
    if fit_path.stem.startswith(f"{prefix}_"):
        # Reprocessing an already-renamed file. Prefixing again would stack a
        # second date onto the name and, because the result differs from every
        # name in completed/, would slip past the no-overwrite guard below and
        # leave two files for one activity.
        logger.debug(
            "%s is already prefixed with its start date; keeping it",
            fit_path.name,
        )
        return fit_path.name

    return f"{prefix}_{fit_path.stem}{fit_path.suffix}"


def _apply_activity_mtime(
    dest: Path,
    original_name: str,
    payload: dict[str, Any] | None,
) -> None:
    """Set a completed file's mtime to the instant the activity started.

    This is what makes ``ls -t`` and a file manager's date column agree with the
    filename. It is opt-in via ``COMPLETED_SET_MTIME``, because it discards the
    only record of when the file arrived.

    The source is ``started_at``, the absolute UTC instant — not the local wall
    clock. An mtime is a point in time, so the OS renders it in the *viewer's*
    timezone: an activity recorded abroad shows a clock time that differs from
    its filename by the travel offset, while still sorting correctly.

    A failure here is a WARNING, not fatal — the file was already processed,
    delivered, and moved.

    Args:
        dest: Path to the file in completed/.
        original_name: Received filename, for log messages.
        payload: Delivered payload, used to source the UTC start time.
    """
    started_at = (payload or {}).get("activity", {}).get("started_at")
    if not started_at:
        logger.warning(
            "%s has no start time; leaving its modification time unchanged",
            original_name,
        )
        return

    try:
        parsed = datetime.fromisoformat(started_at)
    except ValueError:
        logger.warning(
            "%s has an unparseable start time %r; leaving its modification "
            "time unchanged",
            original_name,
            started_at,
        )
        return

    if parsed.tzinfo is None:
        # No timezone means no absolute instant, and the framework does not
        # assume one. Better an untouched mtime than a wrong one.
        logger.warning(
            "%s has a start time without a UTC offset (%r); leaving its "
            "modification time unchanged",
            original_name,
            started_at,
        )
        return

    epoch = parsed.timestamp()
    try:
        os.utime(dest, (epoch, epoch))
    except OSError as exc:
        logger.warning(
            "Could not set the modification time on completed/%s: %s",
            dest.name,
            exc,
        )
        return

    logger.debug("Set mtime on completed/%s to %s", dest.name, started_at)


def move_to_completed(
    fit_path: Path,
    completed_dir: Path,
    config: Config | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Move a processed FIT file to the completed directory.

    A failure here is a WARNING, not fatal — the file was already
    successfully processed and delivered.

    Args:
        fit_path: Path to the processed FIT file.
        completed_dir: Destination completed/ directory.
        config: Loaded pipeline configuration. When None, neither renaming nor
            mtime rewriting occurs.
        payload: Delivered payload, used to source the activity start times.
    """
    name = fit_path.name
    if config is not None:
        name = _completed_filename(fit_path, config, payload)

    try:
        completed_dir.mkdir(exist_ok=True)
        dest = completed_dir / name

        # Never overwrite: the existing file is a previously completed activity.
        if name != fit_path.name and dest.exists():
            logger.warning(
                "completed/%s already exists; keeping the original filename for %s",
                name,
                fit_path.name,
            )
            dest = completed_dir / fit_path.name

        shutil.move(str(fit_path), str(dest))
        if dest.name != fit_path.name:
            logger.info("Moved %s → completed/%s", fit_path.name, dest.name)
        else:
            logger.debug("Moved %s → completed/", fit_path.name)

        # Only after the move — utime must target the file's final location.
        if config is not None and config.completed_set_mtime:
            _apply_activity_mtime(dest, fit_path.name, payload)
    except OSError as exc:
        logger.warning(
            "Could not move %s to completed/: %s — file was successfully processed",
            fit_path.name,
            exc,
        )
