"""Base Processor class — the middleware interface for fit-pipeline."""

import logging
from abc import ABC, abstractmethod
from typing import Any

from fit_pipeline.config import Config

logger = logging.getLogger(__name__)


class Processor(ABC):
    """Abstract base class for all middleware processors.

    Processors are registered as a list and executed in sequence. Each processor
    receives the output of the previous one. The final processor's output is
    delivered to the configured output target.

    Subclasses must implement :meth:`process`. The method must return a dict —
    returning None is an error. Raising an exception halts the pipeline.

    Example::

        class MyProcessor(Processor):
            def process(self, data: dict[str, Any]) -> dict[str, Any]:
                data["my_field"] = "my_value"
                return data
    """

    def __init__(self, config: Config) -> None:
        """Initialize the processor.

        Args:
            config: Loaded pipeline configuration.
        """
        self.config = config

    @abstractmethod
    def process(self, data: dict[str, Any]) -> dict[str, Any]:
        """Transform the activity data dict and return the modified dict.

        Args:
            data: Parsed (and previously-processed) activity data.

        Returns:
            Modified activity data dict. Must not be None.

        Raises:
            Any exception: halts the pipeline with exit code 1.
        """
        raise NotImplementedError
