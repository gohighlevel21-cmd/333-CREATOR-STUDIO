from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

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
GUEST_WINDOW = 24 * 60 * 60
GUEST_STORE_LIMIT = 30
TURNSTILE_ACTION = "creator_studio"


class GuestLimitReached(Exception):
    pass


def verify_turnstile(token: Any, secret: str, hostname: str) -> bool:
    if not isinstance(token, str) or not 0 < len(token) <= 2048:
        return False
    request = Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=urlencode({"secret": secret, "response": token}).encode(),
    )
    with urlopen(request, timeout=8) as response:
        result = json.load(response)
    return (
        result.get("success") is True
        and result.get("action") == TURNSTILE_ACTION
        and result.get("hostname") == hostname
    )


def reserve_guest_request(
    database_url: str, guest: str, revision: int, reference_url: str | None
) -> int:
    import psycopg

    now = int(time.time())
    cutoff = now - GUEST_WINDOW
    with psycopg.connect(database_url, connect_timeout=5) as connection:
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SELECT pg_advisory_xact_lock(3331717)")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS guest_requests (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                guest TEXT NOT NULL, revision INTEGER NOT NULL,
                started BIGINT NOT NULL, image_url TEXT
            )"""
        )
        connection.execute("DELETE FROM guest_requests WHERE started <= %s", (cutoff,))
        count = connection.execute("SELECT COUNT(*) FROM guest_requests").fetchone()[0]
        if count >= GUEST_STORE_LIMIT:
            raise GuestLimitReached("The store's guest AI limit is reached. Try again later.")
        history = connection.execute(
            "SELECT revision, image_url FROM guest_requests WHERE guest = %s ORDER BY id",
            (guest,),
        ).fetchall()
        if revision == 0 and history:
            raise GuestLimitReached("You can start one guest design every 24 hours.")
        if revision and (
            not history or len(history) != revision or history[0][0] != 0
            or history[-1] != (revision - 1, reference_url) or not reference_url
        ):
            raise GuestLimitReached("Revise your most recent guest design in order, up to twice.")
        cursor = connection.execute(
            "INSERT INTO guest_requests (guest, revision, started) VALUES (%s, %s, %s) RETURNING id",
            (guest, revision, now),
        )
        return cursor.fetchone()[0]


def record_guest_image(database_url: str, request_id: int, image_url: str) -> None:
    import psycopg

    with psycopg.connect(database_url, connect_timeout=5) as connection:
        connection.execute(
            "UPDATE guest_requests SET image_url = %s WHERE id = %s",
            (image_url, request_id),
        )


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
        if not os.getenv("HF_KEY"):
            self._write_json(503, {"error": "Generation is not configured."})
            return
        authorization = self.headers.get("Authorization", "").encode()
        preview = bool(token) and secrets.compare_digest(authorization, ("Bearer " + token).encode())
        guest_enabled = os.getenv("CREATOR_STUDIO_GUEST_ENABLED") == "1"
        if not preview and not guest_enabled:
            self._write_json(401, {"error": "Preview access required."})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise ValueError("The request is empty or exceeds the 25 MB upload limit.")
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("The request body must be a JSON object.")
            if preview:
                self._write_json(200, generate(payload))
                return
            secret = os.getenv("TURNSTILE_SECRET_KEY")
            database_url = os.getenv("CREATOR_STUDIO_QUOTA_DATABASE_URL")
            if not secret or not database_url:
                self._write_json(503, {"error": "Guest generation is temporarily unavailable."})
                return
            revision = payload.get("revision", 0)
            if type(revision) is not int or revision not in {0, 1, 2}:
                raise ValueError("Only the initial design and two revisions are supported.")
            prompt = payload.get("prompt")
            wording = payload.get("wording", "")
            prompt_limit = 500 if revision else 2500
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > prompt_limit:
                raise ValueError(f"Enter a description up to {prompt_limit:,} characters.")
            if not isinstance(wording, str) or len(wording) > 48:
                raise ValueError("Exact wording must be 48 characters or fewer.")
            reference_url = _validated_reference_url(payload.get("reference_url"))
            if revision == 0 and reference_url:
                raise ValueError("Choose a reference file to start a guest design.")
            hostname = urlparse(self._origin()).hostname
            if not verify_turnstile(payload.get("turnstile_token"), secret, hostname):
                self._write_json(403, {"error": "Please complete the verification and try again."})
                return
            address = ipaddress.ip_address(self.headers.get("X-Real-IP", ""))
            if isinstance(address, ipaddress.IPv6Address):
                address = ipaddress.ip_network((address, 64), strict=False).network_address
            guest = hmac.new(secret.encode(), str(address).encode(), hashlib.sha256).hexdigest()
            request_id = reserve_guest_request(database_url, guest, revision, reference_url)
            result = generate(payload)
            record_guest_image(database_url, request_id, result["image_url"])
            self._write_json(200, result)
        except GuestLimitReached as exc:
            self._write_json(429, {"error": str(exc)})
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
