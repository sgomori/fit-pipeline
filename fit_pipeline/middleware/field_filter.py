"""FieldFilterProcessor — include or exclude specific payload fields."""

import logging
from typing import Any

from fit_pipeline.config import Config
from fit_pipeline.processor import Processor

logger = logging.getLogger(__name__)


class FieldFilterProcessor(Processor):
    """Remove unwanted fields from the payload activity dict.

    Reads the exclude_fields list from config and removes matching keys from
    the top-level activity dict and the streams dict. This is a demonstration
    processor — the parser already handles primary exclusions (GPS, device info).
    Use this processor to further filter fields at the payload level.

    Example use case: excluding temperature_celsius before delivery to a
    consumer that doesn't use it.

    Config::

        EXCLUDE_FIELDS=temperature_celsius,average_cadence
    """

    def __init__(self, config: Config) -> None:
        """Initialize with pipeline config.

        Args:
            config: Loaded pipeline configuration.
        """
        super().__init__(config)
        self._exclude = set(config.exclude_fields)
        if self._exclude:
            logger.debug("FieldFilterProcessor will exclude: %s", self._exclude)

    def process(self, data: dict[str, Any]) -> dict[str, Any]:
        """Remove configured fields from the activity and streams sections.

        Args:
            data: Activity payload dict from parser or previous processor.

        Returns:
            Payload with configured fields removed.
        """
        if not self._exclude:
            return data

        activity = data.get("activity", {})
        before = len(activity)
        data["activity"] = {k: v for k, v in activity.items() if k not in self._exclude}
        removed = before - len(data["activity"])

        streams = data.get("streams", {})
        data["streams"] = {k: v for k, v in streams.items() if k not in self._exclude}

        if removed:
            logger.debug("FieldFilterProcessor removed %d field(s)", removed)

        return data
