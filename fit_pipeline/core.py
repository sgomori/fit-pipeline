"""Core pipeline orchestration — processes a single FIT file end-to-end.

Shared by both the CLI (pipeline.py) and HTTP server (fit_pipeline/server.py).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fit_pipeline.config import Config
from fit_pipeline.delivery import make_delivery
from fit_pipeline.exceptions import DeliveryError, MiddlewareError, ParseError
from fit_pipeline.parser import parse_fit_file
from fit_pipeline.processor import Processor

logger = logging.getLogger(__name__)

# Webhook contract version. 1.1 added activity.started_at_local and
# activity.utc_offset_seconds; both are additive and may be absent.
SCHEMA_VERSION = "1.1"


def process_file(
    path: str | Path,
    processors: list[Processor],
    config: Config,
) -> dict[str, Any]:
    """Parse, process through middleware, and deliver a single FIT file.

    Args:
        path: Path to the .fit file.
        processors: Instantiated processor chain to run in sequence.
        config: Loaded pipeline configuration.

    Returns:
        The final delivered payload dict.

    Raises:
        ParseError: If the FIT file is malformed or unreadable.
        MiddlewareError: If any processor raises an exception.
        DeliveryError: If payload delivery fails after retry.
    """
    path = Path(path)
    logger.info("Processing: %s", path.name)

    # Parse
    data = parse_fit_file(path, config)

    # Run middleware chain
    for proc in processors:
        proc_name = type(proc).__name__
        logger.debug("Running processor: %s", proc_name)
        try:
            result = proc.process(data)
        except (ParseError, DeliveryError, MiddlewareError):
            raise
        except Exception as exc:
            raise MiddlewareError(
                f"Processor {proc_name} raised an exception: {exc}"
            ) from exc

        if result is None:
            raise MiddlewareError(
                f"Processor {proc_name}.process() returned None — must return a dict"
            )
        data = result

    # Build final payload
    payload = _build_payload(data, path.name, config)

    # Deliver
    delivery = make_delivery(config)
    delivery.deliver(payload)

    logger.info("Done: %s", path.name)
    return payload


def _build_payload(data: dict[str, Any], filename: str, config: Config | None = None) -> dict[str, Any]:
    """Assemble the final deliverable payload with envelope fields.

    Args:
        data: Processed activity data dict from the middleware chain.
        filename: Source filename for the payload envelope.

    Returns:
        Complete payload dict with schema_version, source, file,
        processed_at, activity, and optional computed_metrics and streams.
    """
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": "garmin_fit",
        "file": filename,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "activity": data.get("activity", {}),
    }

    if data.get("laps"):
        payload["laps"] = data["laps"]

    if "computed_metrics" in data:
        payload["computed_metrics"] = data["computed_metrics"]

    include_streams = config.include_streams if config else True
    if include_streams and data.get("streams"):
        payload["streams"] = data["streams"]

    return payload


def build_processor_chain(
    processor_classes: list[type[Processor]],
    config: Config,
) -> list[Processor]:
    """Instantiate each processor class with config.

    Args:
        processor_classes: List of Processor subclasses (from processors.py).
        config: Loaded pipeline configuration.

    Returns:
        List of instantiated processors ready to run.
    """
    return [cls(config) for cls in processor_classes]
