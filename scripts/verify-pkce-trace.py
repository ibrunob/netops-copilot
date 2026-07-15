#!/usr/bin/env python3
"""Run a real local Keycloak PKCE -> API -> Tempo verification.

The script intentionally never prints, stores, or accepts a password or access
token. A user signs in to the local Keycloak page in their own browser; the
short-lived authorization code is returned only to this loopback listener and
exchanged in memory for the duration of the trace check.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8765
CALLBACK_PATH = "/callback"
CLIENT_ID = "netops-web"


def env_port(name: str, default: int) -> int:
    """Read one safe integer port from the ignored local environment file."""
    environment_file = ROOT_DIR / ".env"
    if not environment_file.is_file():
        raise RuntimeError("Missing .env. Run 'make env' first.")
    for line in environment_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            value = line.partition("=")[2].strip()
            try:
                return int(value)
            except ValueError as error:
                raise RuntimeError(f"{name} must be an integer port.") from error
    return default


class CallbackHandler(BaseHTTPRequestHandler):
    """Receive exactly one authorization response on the loopback interface."""

    expected_state: ClassVar[str]
    authorization_code: ClassVar[str | None] = None
    callback_error: ClassVar[str | None] = None

    def do_GET(self) -> None:  # noqa: N802 - HTTP handler API requires this name.
        parsed = urlparse(self.path)
        parameters = parse_qs(parsed.query)
        code = parameters.get("code", [None])[0]
        state = parameters.get("state", [None])[0]
        error = parameters.get("error", [None])[0]

        if parsed.path != CALLBACK_PATH or state != self.expected_state:
            self.callback_error = "The callback path or state did not match this verification run."
            self._respond(400, "Verification failed. Return to the terminal.")
            return
        if error or not code:
            self.callback_error = "Keycloak did not return an authorization code."
            self._respond(400, "Keycloak sign-in was not completed. Return to the terminal.")
            return

        self.authorization_code = code
        self._respond(200, "Sign-in received. You may return to the terminal.")

    def log_message(self, _: str, *__: object) -> None:
        """Do not write callback paths, codes, or browser metadata to stdout."""

    def _respond(self, status: int, message: str) -> None:
        response = f"<!doctype html><title>NetOps Copilot verification</title><p>{message}</p>"
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(response.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(response.encode("utf-8"))


def request_json(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    """Perform a bounded local HTTP request without rendering sensitive responses."""
    request = Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    with urlopen(request, timeout=5) as response:  # noqa: S310 - fixed local URLs only.
        return response.read()


def receive_authorization_code(authorization_url: str, state: str, timeout_seconds: int) -> str:
    """Wait for a user-completed browser PKCE callback on loopback only."""
    CallbackHandler.expected_state = state
    CallbackHandler.authorization_code = None
    CallbackHandler.callback_error = None
    try:
        server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), CallbackHandler)
    except OSError as error:
        raise RuntimeError(
            f"Could not bind {CALLBACK_HOST}:{CALLBACK_PORT}; stop the process using that loopback port."
        ) from error

    with server:
        server.timeout = 1
        print("Open this URL in a browser where you can sign in to local Keycloak:")
        print(authorization_url)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            server.handle_request()
            if CallbackHandler.callback_error:
                raise RuntimeError(CallbackHandler.callback_error)
            if CallbackHandler.authorization_code:
                return CallbackHandler.authorization_code
    raise RuntimeError("Timed out waiting for the Keycloak sign-in callback.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=300, help="Browser sign-in timeout in seconds.")
    arguments = parser.parse_args()
    if arguments.timeout < 30 or arguments.timeout > 900:
        parser.error("--timeout must be between 30 and 900 seconds")

    keycloak_port = env_port("KEYCLOAK_PORT", 8080)
    api_port = env_port("NETOPS_PORT", 8000)
    tempo_port = env_port("TEMPO_HTTP_PORT", 3200)
    issuer = f"http://localhost:{keycloak_port}/realms/netops-dev"
    redirect_uri = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    authorization_url = (
        f"{issuer}/protocol/openid-connect/auth?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": CLIENT_ID,
                "redirect_uri": redirect_uri,
                "scope": "openid",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )

    try:
        code = receive_authorization_code(authorization_url, state, arguments.timeout)
        token_response = request_json(
            f"{issuer}/protocol/openid-connect/token",
            urlencode(
                {
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": verifier,
                }
            ).encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
    except (HTTPError, URLError, RuntimeError) as error:
        print(f"PKCE verification could not obtain a local access token: {error}", file=sys.stderr)
        return 1

    import json

    bearer = json.loads(token_response).get("access_token")
    if not isinstance(bearer, str) or not bearer:
        print("Keycloak did not return an access token.", file=sys.stderr)
        return 1

    trace_id = secrets.token_hex(16)
    traceparent = f"00-{trace_id}-{secrets.token_hex(8)}-01"
    try:
        request = Request(
            f"http://127.0.0.1:{api_port}/v1/auth/me",
            headers={"Authorization": f"Bearer {bearer}", "traceparent": traceparent},
        )
        with urlopen(request, timeout=5) as response:  # noqa: S310 - fixed local URL only.
            returned_traceparent = response.headers.get("traceparent", "")
            response.read()
        if not returned_traceparent.startswith(f"00-{trace_id}-"):
            raise RuntimeError("The API did not retain the W3C trace ID.")

        deadline = time.monotonic() + 30
        trace_url = f"http://127.0.0.1:{tempo_port}/api/traces/{trace_id}"
        while time.monotonic() < deadline:
            try:
                request_json(trace_url)
                print(f"Signed OIDC trace was found in Tempo: {trace_id}")
                return 0
            except (HTTPError, URLError):
                time.sleep(1)
    except (HTTPError, URLError, RuntimeError) as error:
        print(f"Signed API trace verification failed: {error}", file=sys.stderr)
        return 1
    finally:
        bearer = ""
        verifier = ""

    print(f"Timed out waiting for signed trace {trace_id} in Tempo.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
