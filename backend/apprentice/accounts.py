"""Who is asking: an anonymous device, a signed-in person, and the workspace they're working in.

- Shoppers never sign in. A random device id (cookie) keeps their sessions theirs.
- Brands and employers create a workspace (email + password). Employees join with the workspace's join
  code and their name, no password, from their own phone; their training lands on the workspace's records.
- Product guides are shared: a manual or store link read once serves everyone.

Passwords are hashed with scrypt; sign-in tokens are random and stored only as SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

DEVICE_COOKIE = "sm_device"
AUTH_COOKIE = "sm_auth"
WS_COOKIE = "sm_ws"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: Optional[str]) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, digest = stored.split("$")
    test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(test.hex(), digest)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_device_id() -> str:
    return "dev_" + secrets.token_urlsafe(12)


@dataclass
class Identity:
    device_id: str
    ip: str = ""
    user: Optional[dict] = None
    workspaces: list[dict] = field(default_factory=list)
    workspace: Optional[dict] = None  # the one they're working in now

    @property
    def user_id(self) -> Optional[str]:
        return self.user["id"] if self.user else None

    @property
    def workspace_id(self) -> Optional[str]:
        return self.workspace["id"] if self.workspace else None

    @property
    def workspace_ids(self) -> list[str]:
        return [w["id"] for w in self.workspaces]

    def member_of(self, wid: Optional[str]) -> bool:
        return bool(wid) and wid in self.workspace_ids

    def owner_of(self, wid: Optional[str]) -> bool:
        return any(w["id"] == wid and w["role"] == "owner" for w in self.workspaces)

    @property
    def key(self) -> str:
        """For rate limits: the person if signed in, else the device, else the address."""
        return self.user_id or self.device_id or self.ip


class RateLimiter:
    """Sliding-window limits per person, per kind of request. In memory: per server process.
    Each process enforces its own share, which bounds the total at (limit x processes)."""

    def __init__(self) -> None:
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def allow(self, bucket: str, key: str, limit: int, window_s: float) -> tuple[bool, int]:
        if limit <= 0:
            return True, 0
        t = time.monotonic()
        q = self.hits[(bucket, key)]
        while q and t - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False, int(window_s - (t - q[0])) + 1
        q.append(t)
        if len(self.hits) > 50_000:  # forget idle keys so memory stays bounded
            for k in [k for k, v in self.hits.items() if not v or t - v[-1] > 3600][:10_000]:
                self.hits.pop(k, None)
        return True, 0
