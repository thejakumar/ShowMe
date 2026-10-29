"""Records every model call: a JSONL file per day, plus a live stream for the trace panel.

Each line is proof of which Muse model did which job, how long it took, and whether
the data left the building (tag: cloud, local or mock).
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator


class Tracer:
    def __init__(self, trace_dir: Path, keep: int = 500) -> None:
        self.dir = trace_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.recent: deque[dict[str, Any]] = deque(maxlen=keep)
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()

    def record(self, **fields: Any) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        entry = {"time": now.isoformat(timespec="milliseconds"), **fields}
        path = self.dir / f"{now:%Y-%m-%d}.jsonl"
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self.recent.append(entry)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:  # a slow viewer misses entries rather than blocking calls
                pass
        return entry

    def latest(self, limit: int = 100) -> list[dict[str, Any]]:
        items = list(self.recent)[-limit:]
        return list(reversed(items))

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)
