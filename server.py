#!/usr/bin/env python3
"""fit-pipeline HTTP server entry point.

Usage:
    python server.py
    uvicorn server:app --host 0.0.0.0 --port 8000

Environment:
    SERVER_SECRET  — required Bearer token for /process endpoint
    SERVER_PORT    — port to bind (default: 8000)
    All other pipeline variables from .env.example apply.
"""

import logging
import sys

from fit_pipeline.config import ConfigError, configure_logging, load_config
from fit_pipeline.server import create_app

# Import processor chain from project config module
from processors import PROCESSOR_CHAIN

try:
    config = load_config()
except ConfigError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    sys.exit(1)

configure_logging(config)
logger = logging.getLogger(__name__)

if not config.server_secret:
    logger.error("SERVER_SECRET is required to run the HTTP server")
    sys.exit(1)

app = create_app(config, PROCESSOR_CHAIN)

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting fit-pipeline server on port %d", config.server_port)
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=config.server_port,
        log_level=config.log_level.lower(),
    )
