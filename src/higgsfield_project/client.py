"""Server-side helpers around the official Higgsfield Python SDK."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional

import higgsfield_client
from dotenv import load_dotenv


class HiggsfieldConfigurationError(RuntimeError):
    """Raised when Higgsfield credentials have not been configured."""


def configure_from_env() -> None:
    """Load a local .env file and verify that SDK credentials are present."""

    load_dotenv()
    has_combined_key = bool(os.getenv("HF_KEY"))
    has_split_key = bool(os.getenv("HF_API_KEY") and os.getenv("HF_API_SECRET"))

    if not (has_combined_key or has_split_key):
        raise HiggsfieldConfigurationError(
            "Missing Higgsfield credentials. Set HF_KEY in .env to "
            "'your-api-key-id:your-api-key-secret'."
        )


def run_model(
    endpoint: str,
    arguments: Mapping[str, Any],
    *,
    webhook_url: Optional[str] = None,
) -> dict[str, Any]:
    """Submit any documented model endpoint and wait for its final result."""

    configure_from_env()
    model_arguments = dict(arguments)

    # The SDK's convenience subscribe() call does not accept a webhook. When a
    # webhook is requested, submit explicitly and wait through its controller.
    if webhook_url:
        controller = higgsfield_client.submit(
            endpoint,
            arguments=model_arguments,
            webhook_url=webhook_url,
        )
        return controller.get()

    return higgsfield_client.subscribe(endpoint, arguments=model_arguments)


def submit_model(
    endpoint: str,
    arguments: Mapping[str, Any],
    *,
    webhook_url: Optional[str] = None,
) -> Any:
    """Submit any documented model endpoint and return its request controller."""

    configure_from_env()
    return higgsfield_client.submit(
        endpoint,
        arguments=dict(arguments),
        webhook_url=webhook_url,
    )


def status(request_id: str) -> Any:
    """Return the latest status for an existing request."""

    configure_from_env()
    return higgsfield_client.status(request_id=request_id)


def result(request_id: str) -> dict[str, Any]:
    """Wait for and return the result of an existing request."""

    configure_from_env()
    return higgsfield_client.result(request_id=request_id)


def cancel(request_id: str) -> Any:
    """Cancel an existing request if it is still eligible for cancellation."""

    configure_from_env()
    return higgsfield_client.cancel(request_id=request_id)


def upload_file(path: str | Path) -> str:
    """Upload local media and return the URL to pass in a model's input."""

    configure_from_env()
    return higgsfield_client.upload_file(str(path))
