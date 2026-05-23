#!/usr/bin/env python3
"""fit-pipeline CLI entry point.

Usage:
    python pipeline.py /path/to/activity.fit
    python pipeline.py /path/to/fit_files/
    python pipeline.py /path/to/activity.fit --dry-run
    python pipeline.py /path/to/activity.fit --output /path/to/output.json
"""

import argparse
import logging
import sys
from pathlib import Path

from fit_pipeline.batch import process_directory
from fit_pipeline.config import Config, configure_logging, load_config
from fit_pipeline.core import build_processor_chain, process_file
from fit_pipeline.exceptions import ConfigError, DeliveryError, MiddlewareError, ParseError

# Import processor chain from project config module
from processors import PROCESSOR_CHAIN

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="Parse a Garmin FIT file and deliver structured JSON to a webhook.",
    )
    parser.add_argument(
        "path",
        help="Path to a .fit file or a directory of .fit files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and process without delivering; write payload to stdout",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write payload to FILE instead of POSTing to the webhook",
    )
    parser.add_argument(
        "--env-file",
        metavar="FILE",
        default=None,
        help="Path to a .env file (defaults to .env in the working directory)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the pipeline CLI.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Exit code: 0 on success, 1 on any failure.
    """
    args = _parse_args(argv)

    try:
        config = load_config(args.env_file)
    except ConfigError as exc:
        # Logging not configured yet — use stderr directly
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # CLI flags override .env settings
    if args.dry_run:
        config.dry_run = True
    if args.output:
        config.output_file = args.output

    configure_logging(config)

    try:
        processors = build_processor_chain(PROCESSOR_CHAIN, config)
    except Exception as exc:
        logger.error("Failed to initialize processor chain: %s", exc)
        return 1

    target = Path(args.path)

    try:
        if target.is_dir():
            process_directory(target, processors, config)
        else:
            process_file(target, processors, config)
    except (ParseError, MiddlewareError, DeliveryError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        logger.error("Unexpected error: %s", exc, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
