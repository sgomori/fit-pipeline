"""Example custom processor implementation.

Copy this file as a starting point for your own middleware processors.
Drop it anywhere in your project and add it to the PROCESSOR_CHAIN in
processors.py.
"""

import logging

from fit_pipeline.config import Config
from fit_pipeline.processor import Processor

logger = logging.getLogger(__name__)


class ExampleProcessor(Processor):
    """Demonstrate the Processor interface with a simple transformation.

    This processor adds a custom field to the activity dict. Replace the
    process() body with your own transformation logic.

    To activate::

        # In processors.py:
        from examples.custom_processor import ExampleProcessor

        PROCESSOR_CHAIN = [
            StandardAnalyticsProcessor,
            ExampleProcessor,
        ]
    """

    def __init__(self, config: Config) -> None:
        """Initialize with pipeline config.

        Args:
            config: Loaded pipeline configuration. Access via self.config.
        """
        super().__init__(config)
        logger.debug("ExampleProcessor initialized")

    def process(self, data: dict) -> dict:
        """Transform the activity data.

        Receives the full payload dict (activity, streams, computed_metrics,
        etc.) from the previous processor in the chain. Must return a dict.

        Args:
            data: Activity payload from the parser or previous processor.

        Returns:
            Modified payload dict. Never return None.
        """
        # Example: add a custom flag field
        activity = data.get("activity", {})
        distance = activity.get("distance_meters", 0) or 0

        if distance >= 21097.5:
            data["activity"]["half_marathon_or_longer"] = True
            logger.info("Activity flagged as half marathon or longer")

        return data
