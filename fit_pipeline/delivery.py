"""Delivery layer — webhook POST and file output.

All HTTP interaction is isolated here. No other module makes HTTP requests.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from fit_pipeline.config import Config
from fit_pipeline.exceptions import DeliveryError

logger = logging.getLogger(__name__)

_RETRY_DELAY_S = 2


class WebhookDelivery:
    """Deliver a payload via HTTP POST with Bearer token auth and single retry.

    Args:
        url: Webhook endpoint URL.
        secret: Bearer token for Authorization header.
    """

    def __init__(self, url: str, secret: str) -> None:
        self.url = url
        self.secret = secret

    def deliver(self, payload: dict[str, Any]) -> None:
        """POST the payload as JSON to the webhook URL.

        Retries once on non-200 response or connection failure.

        Args:
            payload: The processed activity payload dict.

        Raises:
            DeliveryError: If both the initial attempt and retry fail.
        """
        headers = {
            "Authorization": f"Bearer {self.secret}",
            "Content-Type": "application/json",
        }
        body = json.dumps(payload)

        attempt = 1
        while True:
            try:
                response = httpx.post(self.url, content=body, headers=headers, timeout=30)
                if response.status_code == 200:
                    logger.info(
                        "Payload delivered (attempt %d): HTTP %d %s",
                        attempt,
                        response.status_code,
                        self.url,
                    )
                    return

                logger.error(
                    "Webhook returned HTTP %d (attempt %d): %s",
                    response.status_code,
                    attempt,
                    response.text[:200],
                )

            except httpx.RequestError as exc:
                logger.error(
                    "Webhook connection error (attempt %d): %s",
                    attempt,
                    exc,
                )

            if attempt >= 2:
                raise DeliveryError(
                    f"Webhook delivery failed after {attempt} attempt(s): {self.url}"
                )

            logger.info("Retrying in %ds…", _RETRY_DELAY_S)
            time.sleep(_RETRY_DELAY_S)
            attempt += 1


class MultiWebhookDelivery:
    """Deliver a payload to multiple webhook URLs.

    All destinations are always attempted; failures are aggregated. Raises
    DeliveryError after all attempts if any destination failed.

    Args:
        deliveries: WebhookDelivery instances, one per destination URL.
    """

    def __init__(self, deliveries: list[WebhookDelivery]) -> None:
        self.deliveries = deliveries

    def deliver(self, payload: dict[str, Any]) -> None:
        """POST the payload to every configured webhook URL.

        All URLs are attempted even if earlier ones fail. Raises
        DeliveryError listing every URL that failed.

        Args:
            payload: The processed activity payload dict.

        Raises:
            DeliveryError: If one or more destinations could not be reached.
        """
        errors: list[str] = []
        for delivery in self.deliveries:
            try:
                delivery.deliver(payload)
            except DeliveryError as exc:
                errors.append(str(exc))

        if errors:
            raise DeliveryError(
                f"{len(errors)} webhook(s) failed:\n" + "\n".join(errors)
            )


class FileDelivery:
    """Write the payload as formatted JSON to a file path.

    Args:
        path: Destination file path.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def deliver(self, payload: dict[str, Any]) -> None:
        """Write payload to the configured file path.

        Args:
            payload: The processed activity payload dict.

        Raises:
            DeliveryError: If the file cannot be written.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2, default=str))
            logger.info("Payload written to %s", self.path)
        except OSError as exc:
            raise DeliveryError(f"Failed to write payload to {self.path}: {exc}") from exc


class DryRunDelivery:
    """Write the payload to stdout — no HTTP, no file.

    Used when DRY_RUN=true.
    """

    def deliver(self, payload: dict[str, Any]) -> None:
        """Print payload as formatted JSON to stdout.

        Args:
            payload: The processed activity payload dict.
        """
        print(json.dumps(payload, indent=2, default=str))
        logger.info("Dry run: payload written to stdout")


def make_delivery(config: Config) -> MultiWebhookDelivery | FileDelivery | DryRunDelivery:
    """Construct the appropriate delivery object from config.

    Args:
        config: Loaded pipeline configuration.

    Returns:
        Delivery object with a deliver(payload) method.
    """
    if config.dry_run:
        return DryRunDelivery()
    if config.output_file:
        return FileDelivery(config.output_file)
    return MultiWebhookDelivery(
        [WebhookDelivery(dest.url, dest.secret) for dest in config.webhook_destinations]
    )
