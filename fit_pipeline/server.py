"""FastAPI HTTP endpoint for remote pipeline triggering.

Exposes POST /process to accept a file or directory path, authenticate
the caller, and run the pipeline using the same logic as the CLI.
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from fit_pipeline.batch import process_directory
from fit_pipeline.config import Config
from fit_pipeline.core import build_processor_chain, process_file
from fit_pipeline.exceptions import (
    DeliveryError,
    MiddlewareError,
    ParseError,
)
from fit_pipeline.processor import Processor

logger = logging.getLogger(__name__)

app = FastAPI(title="fit-pipeline", version="0.1.0")

_bearer = HTTPBearer()


class ProcessRequest(BaseModel):
    """Request body for POST /process."""

    path: str


class ProcessResponse(BaseModel):
    """Response body for POST /process."""

    status: str
    processed: int
    failed: int
    files: list[dict[str, Any]]


def _get_config(request: Request) -> Config:
    """Retrieve the Config object stored in app state."""
    return request.app.state.config  # type: ignore[no-any-return]


def _get_processors(request: Request) -> list[Processor]:
    """Retrieve the instantiated processor chain from app state."""
    return request.app.state.processors  # type: ignore[no-any-return]


def _authenticate(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),  # noqa: B008
    request: Request = None,  # type: ignore[assignment]
) -> None:
    """Validate the Bearer token against SERVER_SECRET."""
    config: Config = request.app.state.config
    if credentials.credentials != config.server_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
        )


@app.post(
    "/process",
    response_model=ProcessResponse,
    dependencies=[Depends(_authenticate)],
    status_code=200,
)
async def process_endpoint(
    body: ProcessRequest,
    request: Request,
) -> JSONResponse:
    """Process a FIT file or directory of FIT files.

    Authentication is checked before any filesystem access.

    Request body::

        {"path": "/path/to/file.fit"}
        {"path": "/path/to/directory/"}

    Returns a structured JSON response with per-file results.
    """
    config = _get_config(request)
    processors = _get_processors(request)
    target = Path(body.path)

    if not target.exists():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Path not found: {body.path}",
        )

    file_results: list[dict[str, Any]] = []
    failed_count = 0

    try:
        if target.is_dir():
            payloads = process_directory(target, processors, config)
            for payload in payloads:
                file_results.append({"file": payload["file"], "status": "ok"})
        else:
            if target.suffix.lower() != ".fit":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Not a .fit file: {body.path}",
                )
            payload = process_file(target, processors, config)
            file_results.append({"file": payload["file"], "status": "ok"})

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except (ParseError, MiddlewareError, DeliveryError) as exc:
        logger.error("Processing failed: %s", exc)
        failed_count = 1
        failed_file = target.name if target.is_file() else "unknown"
        file_results.append({"file": failed_file, "status": "error", "error": str(exc)})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "error",
                "processed": len(file_results) - 1,
                "failed": failed_count,
                "files": file_results,
            },
        )

    return JSONResponse(
        content={
            "status": "ok",
            "processed": len(file_results),
            "failed": 0,
            "files": file_results,
        }
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Simple health check endpoint."""
    return {"status": "ok"}


def create_app(config: Config, processor_classes: list[type[Processor]]) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Loaded pipeline configuration.
        processor_classes: Processor subclasses from processors.py.

    Returns:
        Configured FastAPI app instance.
    """
    app.state.config = config
    app.state.processors = build_processor_chain(processor_classes, config)
    return app
