from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from higgsfield_project import run_model, upload_file


ENDPOINT = "marketing-studio/image/flare"
MAX_UPLOAD_BYTES = 25_000_000
MAX_REQUEST_BYTES = 34_000_000
ALLOWED_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
DEFAULT_ALLOWED_ORIGINS = {
    "https://333clothingco.net",
    "https://www.333clothingco.net",
}


def _allowed_origins() -> set[str]:
    configured = os.getenv("CREATOR_STUDIO_ALLOWED_ORIGINS", "")
    return DEFAULT_ALLOWED_ORIGINS | {
        origin.strip().rstrip("/") for origin in configured.split(",") if origin.strip()
    }


def _validated_reference_url(value: Any) -> str | None:
    if not value:
        return None
    parsed = urlparse(str(value))
    if parsed.scheme != "https" or parsed.hostname not in {
        "d3u0tzju9qaucj.cloudfront.net",
        "d3snorpfx4xhv8.cloudfront.net",
    }:
        raise ValueError("The revision reference is not a trusted Higgsfield image URL.")
    return str(value)


def _upload_data_url(value: Any) -> str | None:
    if not value:
        return None
    header, separator, encoded = str(value).partition(",")
    if not separator or not header.startswith("data:") or ";base64" not in header:
        raise ValueError("The reference image is not a valid data URL.")
    mime_type = header[5:].split(";", 1)[0]
    suffix = ALLOWED_MIME_TYPES.get(mime_type)
    if not suffix:
        raise ValueError("Reference images must be PNG, JPG, or WebP.")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("The reference image could not be decoded.") from exc
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("Reference images must be 25 MB or smaller.")
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(payload)
            temporary_path = temporary.name
        return upload_file(temporary_path)
    finally:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)


def generate(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt", "")).strip()
    wording = str(payload.get("wording", "")).strip()
    revision = int(payload.get("revision", 0))
    if revision not in {0, 1, 2}:
        raise ValueError("Only the initial design and two revisions are supported.")
    if not prompt:
        raise ValueError("Describe the design or revision you want.")

    reference_url = _validated_reference_url(payload.get("reference_url"))
    if revision == 0 and not reference_url:
        reference_url = _upload_data_url(payload.get("reference_data_url"))
    if revision and not reference_url:
        raise ValueError("A generated design is required before revising it.")

    exact_wording = f' The artwork must include the exact wording: "{wording}".' if wording else ""
    instruction = (
        f"Create one print-ready apparel graphic from this description: {prompt}.{exact_wording} "
        "Return only the flat square artwork, not a shirt mockup. Use a clean solid background, "
        "crisp edges, high contrast, no person, no watermark, and no extra text."
        if revision == 0
        else f"Revise the supplied artwork with this instruction: {prompt}.{exact_wording} "
        "Preserve everything not explicitly changed. Return only the flat square artwork, "
        "not a shirt mockup, with no watermark."
    )
    arguments: dict[str, Any] = {
        "prompt": instruction,
        "quality": "low",
        "moderation": "auto",
        "resolution": "1k",
        "aspect_ratio": "1:1",
        "enhance_prompt": False,
    }
    if reference_url:
        arguments["image_urls"] = [reference_url]
    result = run_model(ENDPOINT, arguments)
    images = result.get("images") or []
    if not images or not images[0].get("url"):
        raise RuntimeError("Higgsfield completed without returning an image.")
    return {
        "image_url": images[0]["url"],
        "request_id": result.get("request_id"),
        "revision": revision,
    }


class CreatorStudioHandler(BaseHTTPRequestHandler):
    server_version = "CreatorStudioBridge/1.0"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._write_json(200, {"status": "ok"})
        else:
            self._write_json(404, {"error": "Not found."})

    def _origin(self) -> str | None:
        origin = self.headers.get("Origin", "").rstrip("/")
        return origin if origin in _allowed_origins() else None

    def _write_json(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        origin = self._origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if self.path != "/api/creator-studio/generate" or not self._origin():
            self._write_json(403, {"error": "Origin not allowed."})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self._origin() or "")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/api/creator-studio/generate":
            self._write_json(404, {"error": "Not found."})
            return
        if not self._origin():
            self._write_json(403, {"error": "Origin not allowed."})
            return
        token = os.getenv("CREATOR_STUDIO_PREVIEW_TOKEN", "")
        if not token or not os.getenv("HF_KEY"):
            self._write_json(503, {"error": "Generation is not configured."})
            return
        authorization = self.headers.get("Authorization", "").encode()
        if not secrets.compare_digest(authorization, ("Bearer " + token).encode()):
            self._write_json(401, {"error": "Preview access required."})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise ValueError("The request is empty or exceeds the 25 MB upload limit.")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("The request body must be a JSON object.")
            self._write_json(200, generate(payload))
        except (ValueError, json.JSONDecodeError) as exc:
            self._write_json(400, {"error": str(exc)})
        except Exception:
            self._write_json(502, {"error": "The design could not be generated. Please try again."})


def main() -> None:
    project_dir = Path(
        os.getenv(
            "HIGGSFIELD_PROJECT_DIR",
            "/Users/toplevelcreations/Documents/Codex/2026-10-03/set-up-this-project-to-use",
        )
    )
    load_dotenv(project_dir / ".env", override=False)
    port = int(os.getenv("PORT", "8787"))
    host = "0.0.0.0" if os.getenv("RAILWAY_ENVIRONMENT") else "127.0.0.1"
    ThreadingHTTPServer((host, port), CreatorStudioHandler).serve_forever()


if __name__ == "__main__":
    main()
