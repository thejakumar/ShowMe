"""Start Apprentice on the first free port from 8000, print the addresses, and open the browser.

    python backend/scripts/serve.py [--port 8000] [--no-browser]

Why this is careful about ports: on Windows another app can listen on 127.0.0.1:8000 while
Apprentice still manages to bind 0.0.0.0:8000. The browser then reaches the other app
(it shows {"detail": "Not Found"}). So a port counts as free only if nothing answers on it,
and the browser opens only after Apprentice itself has answered.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

BACKEND = Path(__file__).resolve().parents[1]


def answers(host: str, port: int) -> bool:
    """True if something is already listening at host:port."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def can_bind(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: refuse to share a port in use
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def free_port(start: int, tries: int = 30) -> int:
    for port in range(start, start + tries):
        if answers("127.0.0.1", port) or answers("::1", port):
            continue
        if can_bind(port):
            return port
    raise SystemExit(f"No free port between {start} and {start + tries - 1}.")


def lan_ip() -> str:
    """This computer's address on the local network (no traffic is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "YOUR-PC-IP"


def is_apprentice(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=2) as r:
            return json.loads(r.read().decode()).get("app") == "apprentice"
    except Exception:
        return False


def open_when_ready(url: str, open_browser: bool) -> None:
    for _ in range(60):
        if is_apprentice(url):
            print(f"\n  Apprentice is ready: {url}\n", flush=True)
            if open_browser:
                webbrowser.open(url)
            return
        time.sleep(0.5)
    print(f"\n  Apprentice did not answer at {url}. Look for an error above.\n", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    port = free_port(args.port)
    if port != args.port:
        print(f"Port {args.port} is used by another app, so Apprentice uses port {port}.")
    # 127.0.0.1 rather than "localhost": localhost can resolve to IPv6, where another app may listen.
    local = f"http://127.0.0.1:{port}"
    print(f"\n  Apprentice on this PC:      {local}")
    print(f"  On your phone (same Wi-Fi): http://{lan_ip()}:{port}\n", flush=True)
    threading.Thread(target=open_when_ready, args=(local, not args.no_browser), daemon=True).start()
    try:
        uvicorn.run("apprentice.app:app", app_dir=str(BACKEND), host="0.0.0.0", port=port)
    except Exception as e:  # keep the reason on screen
        print(f"\nApprentice stopped: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
