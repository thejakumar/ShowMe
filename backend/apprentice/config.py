"""Runtime settings, read from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:  # .env is optional; real environment variables always win.
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return default if raw is None else raw.strip()


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default_factory=lambda: _str("MODEL_API_KEY", ""))
    base_url: str = field(default_factory=lambda: _str("MODEL_BASE_URL", "https://api.meta.ai/v1").rstrip("/"))
    chat_path: str = field(default_factory=lambda: _str("CHAT_PATH", "/chat/completions"))
    messages_path: str = field(default_factory=lambda: _str("MESSAGES_PATH", "/messages"))
    # auto: chat completions first, Messages API if that endpoint is missing. Or force openai / anthropic.
    api_style: str = field(default_factory=lambda: _str("API_STYLE", "auto"))
    max_tokens: int = field(default_factory=lambda: int(_str("MAX_TOKENS", "8192")))
    spark_model: str = field(default_factory=lambda: _str("SPARK_MODEL", "muse-spark-1.3"))
    wonder_model: str = field(default_factory=lambda: _str("WONDER_MODEL", "muse-wonder-1.0"))
    sam_model: str = field(default_factory=lambda: _str("SAM_MODEL", "sam-3.1"))
    # Reasoning effort is sent only when set; it is dropped automatically if the API rejects it.
    check_effort: str = field(default_factory=lambda: _str("CHECK_EFFORT", "low"))
    plan_effort: str = field(default_factory=lambda: _str("PLAN_EFFORT", "medium"))
    # Live camera checks need fast replies; Meta accepts minimal, low, medium, high, xhigh.
    live_effort: str = field(default_factory=lambda: _str("LIVE_EFFORT", "minimal"))
    # Ask the API for {"type": "json_object"} output. Off by default: Meta documents only json_schema, and the
    # prompts plus one automatic repair already produce valid JSON. Dropped automatically if rejected.
    json_mode: bool = field(default_factory=lambda: _bool("JSON_MODE", False))
    # Mock mode returns canned answers so the whole pipeline runs without a key.
    mock: bool = field(default_factory=lambda: _bool("APPRENTICE_MOCK", not os.getenv("MODEL_API_KEY")))

    # Where part finding runs:
    #   spark      - Muse Spark returns boxes (cloud; needs only the API key)
    #   sam_api    - SAM 3.1 on Meta Model API (cloud; endpoint set by SAM_API_PATH)
    #   sam_local  - SAM 3.1 open weights in this process (private; needs a GPU and the sam3 package)
    #   sam_remote - SAM 3.1 on your own server running scripts/vision_server.py (private)
    vision_backend: str = field(default_factory=lambda: _str("VISION_BACKEND", "spark"))
    sam_api_path: str = field(default_factory=lambda: _str("SAM_API_PATH", "/segment"))
    sam_remote_url: str = field(default_factory=lambda: _str("SAM_REMOTE_URL", "").rstrip("/"))
    sam_checkpoint: str = field(default_factory=lambda: _str("SAM_CHECKPOINT", ""))

    data_dir: Path = field(default_factory=lambda: Path(_str("APPRENTICE_DATA", str(ROOT / "data"))))
    trace_dir: Path = field(default_factory=lambda: Path(_str("APPRENTICE_TRACES", str(ROOT / "traces"))))
    # Labels or verdicts below this confidence ask the user instead of guessing.
    min_confidence: float = field(default_factory=lambda: float(_str("MIN_CONFIDENCE", "0.6")))
    max_image_side: int = field(default_factory=lambda: int(_str("MAX_IMAGE_SIDE", "1280")))
    # Live camera checks: smaller frames for speed, and a pass must be this confident to end the step.
    live_image_side: int = field(default_factory=lambda: int(_str("LIVE_IMAGE_SIDE", "896")))
    live_pass_confidence: float = field(default_factory=lambda: float(_str("LIVE_PASS_CONFIDENCE", "0.75")))
    request_timeout: float = field(default_factory=lambda: float(_str("REQUEST_TIMEOUT", "120")))
    max_parallel: int = field(default_factory=lambda: int(_str("MAX_PARALLEL", "4")))
    # Manuals longer than this send only the most relevant pages to Spark.
    max_vision_pages: int = field(default_factory=lambda: int(_str("MAX_VISION_PAGES", "24")))
    max_upload_mb: int = field(default_factory=lambda: int(_str("MAX_UPLOAD_MB", "40")))
    # Address printed in QR codes (an https tunnel or your domain). Empty: this PC's Wi-Fi address and port.
    public_url: str = field(default_factory=lambda: _str("PUBLIC_URL", "").rstrip("/"))

    # ---- hosting for many users
    # postgresql://user:pass@host:5432/db for many users and several servers. Empty: one SQLite file in data/.
    database_url: str = field(default_factory=lambda: _str("DATABASE_URL", ""))
    # Optional direct (unpooled) URL for schema changes; Neon's `neon env pull` writes it next to DATABASE_URL.
    database_url_unpooled: str = field(default_factory=lambda: _str("DATABASE_URL_UNPOOLED", ""))
    # Multi-user: brand dashboards, training records and uploads are private to each workspace, and people sign
    # in. Defaults to on with Postgres and off on a single PC (where everything is visible, as before).
    multi_user: bool = field(default_factory=lambda: _bool("MULTI_USER", bool(os.getenv("DATABASE_URL"))))
    allow_signup: bool = field(default_factory=lambda: _bool("ALLOW_SIGNUP", True))
    # Run the job worker inside the web process (one PC). Set 0 when running separate `python -m apprentice.worker`.
    run_worker: bool = field(default_factory=lambda: _bool("RUN_WORKER", True))
    worker_concurrency: int = field(default_factory=lambda: int(_str("WORKER_CONCURRENCY", "3")))
    # Requests per person (or address) that call Meta's models. Live camera frames have their own, higher limit.
    rate_per_minute: int = field(default_factory=lambda: int(_str("RATE_PER_MINUTE", "30")))
    live_rate_per_minute: int = field(default_factory=lambda: int(_str("LIVE_RATE_PER_MINUTE", "90")))
    uploads_per_hour: int = field(default_factory=lambda: int(_str("UPLOADS_PER_HOUR", "20")))

    @property
    def private_vision(self) -> bool:
        """True when part finding runs on hardware you control (no frames sent to the cloud)."""
        return self.vision_backend in {"sam_local", "sam_remote"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.trace_dir.mkdir(parents=True, exist_ok=True)
    return s
