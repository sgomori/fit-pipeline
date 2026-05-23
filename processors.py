"""Processor chain configuration.

Edit this file to change which middleware processors run and in what order.
Both the CLI (pipeline.py) and HTTP server (server.py) import PROCESSOR_CHAIN
from here.

Each entry in PROCESSOR_CHAIN must be a Processor subclass (not an instance).
The pipeline instantiates each class with the loaded Config before running.
"""

from fit_pipeline.middleware.field_filter import FieldFilterProcessor
from fit_pipeline.middleware.standard_analytics import StandardAnalyticsProcessor

PROCESSOR_CHAIN = [
    StandardAnalyticsProcessor,
    FieldFilterProcessor,
]
