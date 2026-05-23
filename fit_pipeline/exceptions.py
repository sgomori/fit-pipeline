"""Custom exceptions for fit-pipeline."""


class FitPipelineError(Exception):
    """Base exception for all fit-pipeline errors."""


class ParseError(FitPipelineError):
    """Raised when a FIT file cannot be parsed."""


class ConfigError(FitPipelineError):
    """Raised when required configuration is missing or invalid."""


class DeliveryError(FitPipelineError):
    """Raised when payload delivery fails after retry."""


class MiddlewareError(FitPipelineError):
    """Raised when a processor in the middleware chain raises an exception."""
