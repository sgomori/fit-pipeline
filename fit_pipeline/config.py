"""Configuration loading from environment variables.

This is the single point where environment variables are read.
All other components receive configuration as constructor arguments.
"""

import json
import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from fit_pipeline.exceptions import ConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookDestination:
    """A single webhook delivery target with its own authentication secret.

    Attributes:
        url: Webhook endpoint URL (host and port are part of the URL).
        secret: Bearer token used for this destination only.
    """

    url: str
    secret: str


@dataclass
class Config:
    """Complete pipeline configuration."""

    # Delivery
    webhook_destinations: list[WebhookDestination] = field(default_factory=list)
    server_secret: str = ""
    server_port: int = 8000
    upload_dir: str = ""

    # Field filtering
    exclude_gps: bool = True
    exclude_device_info: bool = True
    exclude_fields: list[str] = field(default_factory=list)

    # Streams
    include_streams: bool = True
    stream_sample_rate: int = 3

    # Output mode
    dry_run: bool = False
    output_file: str = ""

    # Logging
    log_level: str = "INFO"
    log_file: str = ""

    # Analytics — LTHR / threshold
    threshold_hr: int | None = None

    # Analytics — HR zones (BPM upper boundaries; None = derive from LTHR %)
    hr_zone_1: int | None = None
    hr_zone_2: int | None = None
    hr_zone_3: int | None = None
    hr_zone_4: int | None = None
    hr_zone_5: int | None = None

    # Analytics — pace zones (s/km upper boundaries)
    pace_zone_easy: int | None = None
    pace_zone_moderate: int | None = None
    pace_zone_threshold: int | None = None

    # Analytics — TRIMP
    resting_hr: int | None = None
    max_hr: int | None = None
    trimp_gender: str = "male"


def _webhook_destinations() -> list[WebhookDestination]:
    """Parse WEBHOOK_DESTINATIONS as a JSON array of {url, secret} objects.

    Returns:
        List of WebhookDestination, empty if the variable is unset.

    Raises:
        ConfigError: If the value is not valid JSON, not an array, or any
            entry is missing a non-empty 'url' or 'secret'.
    """
    raw = os.environ.get("WEBHOOK_DESTINATIONS", "").strip()
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"WEBHOOK_DESTINATIONS must be valid JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise ConfigError(
            "WEBHOOK_DESTINATIONS must be a JSON array of {url, secret} objects"
        )

    destinations: list[WebhookDestination] = []
    for index, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            raise ConfigError(
                f"WEBHOOK_DESTINATIONS[{index}] must be an object with 'url' and 'secret'"
            )
        url = entry.get("url", "")
        secret = entry.get("secret", "")
        if not url or not secret:
            raise ConfigError(
                f"WEBHOOK_DESTINATIONS[{index}] requires non-empty 'url' and 'secret'"
            )
        destinations.append(WebhookDestination(url=url, secret=secret))

    return destinations


def load_config(env_file: str | None = None) -> Config:
    """Load configuration from environment variables and optional .env file.

    Args:
        env_file: Path to a .env file. Defaults to .env in the working directory.

    Returns:
        Populated Config instance.

    Raises:
        ConfigError: If a required variable is missing for the active mode.
    """
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    def _int(key: str, default: int | None = None) -> int | None:
        val = os.environ.get(key)
        if val is None:
            return default
        try:
            return int(val)
        except ValueError as exc:
            raise ConfigError(f"{key} must be an integer, got: {val!r}") from exc

    def _bool(key: str, default: bool) -> bool:
        val = os.environ.get(key, str(default)).lower()
        return val in ("1", "true", "yes")

    def _str_list(key: str) -> list[str]:
        val = os.environ.get(key, "")
        return [v.strip() for v in val.split(",") if v.strip()]

    config = Config(
        webhook_destinations=_webhook_destinations(),
        server_secret=os.environ.get("SERVER_SECRET", ""),
        server_port=_int("SERVER_PORT", 8000) or 8000,
        upload_dir=os.environ.get("UPLOAD_DIR", ""),
        exclude_gps=_bool("EXCLUDE_GPS", True),
        exclude_device_info=_bool("EXCLUDE_DEVICE_INFO", True),
        exclude_fields=_str_list("EXCLUDE_FIELDS"),
        include_streams=_bool("INCLUDE_STREAMS", True),
        stream_sample_rate=_int("STREAM_SAMPLE_RATE", 3) or 3,
        dry_run=_bool("DRY_RUN", False),
        output_file=os.environ.get("OUTPUT_FILE", ""),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        log_file=os.environ.get("LOG_FILE", ""),
        threshold_hr=_int("THRESHOLD_HR"),
        hr_zone_1=_int("HR_ZONE_1"),
        hr_zone_2=_int("HR_ZONE_2"),
        hr_zone_3=_int("HR_ZONE_3"),
        hr_zone_4=_int("HR_ZONE_4"),
        hr_zone_5=_int("HR_ZONE_5"),
        pace_zone_easy=_int("PACE_ZONE_EASY"),
        pace_zone_moderate=_int("PACE_ZONE_MODERATE"),
        pace_zone_threshold=_int("PACE_ZONE_THRESHOLD"),
        resting_hr=_int("RESTING_HR"),
        max_hr=_int("MAX_HR"),
        trimp_gender=os.environ.get("TRIMP_GENDER", "male").lower(),
    )

    _validate(config)
    return config


def _validate(config: Config) -> None:
    """Raise ConfigError for missing required variables."""
    missing = []

    if not config.dry_run and not config.output_file and not config.webhook_destinations:
        missing.append("WEBHOOK_DESTINATIONS")

    if config.trimp_gender not in ("male", "female"):
        raise ConfigError(f"TRIMP_GENDER must be 'male' or 'female', got: {config.trimp_gender!r}")

    if missing:
        raise ConfigError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Set them in your .env file or environment."
        )


def configure_logging(config: Config) -> None:
    """Configure the root logger based on config.

    Args:
        config: Loaded pipeline configuration.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if config.log_file:
        handlers.append(logging.FileHandler(config.log_file))

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
