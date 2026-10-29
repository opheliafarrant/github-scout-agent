"""Desktop entry point used by the frozen PyInstaller build.

Double-clicking the packaged executable starts the local Web UI and opens the
default browser. No command-line arguments are required; when none are given,
the "web" subcommand is implied.

This module exists only for packaging. All behaviour lives in the package
itself so that the CLI, the tests and the frozen build share one code path.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
PORT_SCAN_LIMIT = 20


def _state_directory() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / "GitHubScoutAgent"


def _ensure_std_streams() -> None:
    """Windowed builds have no console, so sys.stdout and sys.stderr are None.

    uvicorn attaches logging handlers to those streams as soon as the server
    starts; without this shim the frozen executable dies on the first log line.
    """

    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = None
    try:
        log_directory = _state_directory() / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        stream = open(log_directory / "web-ui.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = None
    if stream is None:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = stream  # type: ignore[assignment]


def _running_instance(host: str, port: int) -> bool:
    """True when this host/port already serves the GitHub Scout Agent Web UI."""

    url = f"http://{host}:{port}/api/config"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return isinstance(payload, dict) and "ready" in payload


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) != 0


def _choose_port(host: str, preferred: int) -> int:
    for candidate in range(preferred, preferred + PORT_SCAN_LIMIT):
        if _port_is_free(host, candidate):
            return candidate
    return preferred


def main() -> None:
    _ensure_std_streams()

    if len(sys.argv) == 1:
        if _running_instance(DEFAULT_HOST, DEFAULT_PORT):
            # Already running: focus it instead of starting a second server.
            webbrowser.open(f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")
            return
        port = _choose_port(DEFAULT_HOST, DEFAULT_PORT)
        sys.argv = [sys.argv[0], "web", "--host", DEFAULT_HOST, "--port", str(port)]

    from github_scout_agent.cli import app

    app()


if __name__ == "__main__":
    main()
