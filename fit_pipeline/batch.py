"""Batch processing — iterate a directory of FIT files safely.

Completed files are moved to a completed/ subdirectory before the next file
is attempted, making the batch safe to restart after interruption.
"""

import logging
import shutil
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

        _move_to_completed(fit_path, completed_dir)

    logger.info(
        "Batch complete: %d/%d files processed", len(results), len(fit_files)
    )
    return results


def _move_to_completed(fit_path: Path, completed_dir: Path) -> None:
    """Move a processed FIT file to the completed directory.

    A failure here is a WARNING, not fatal — the file was already
    successfully processed and delivered.

    Args:
        fit_path: Path to the processed FIT file.
        completed_dir: Destination completed/ directory.
    """
    try:
        completed_dir.mkdir(exist_ok=True)
        dest = completed_dir / fit_path.name
        shutil.move(str(fit_path), str(dest))
        logger.debug("Moved %s → completed/", fit_path.name)
    except OSError as exc:
        logger.warning(
            "Could not move %s to completed/: %s — file was successfully processed",
            fit_path.name,
            exc,
        )
