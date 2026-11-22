import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def utc_date() -> date:
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True)
class Settings:
    backend_url: str = "http://127.0.0.1:8765"
    database: Path = ROOT / "var" / "assistant.sqlite3"
    trace_path: Path | None = ROOT / "var" / "traces.jsonl"
    mcp_server: Path = ROOT / "mcp_server.py"
    python: str = sys.executable
    mcp_timeout: float = 8.0
    http_timeout: float = 3.0
    read_attempts: int = 2
    publish_attempts: int = 2
    # A draft states date-dependent facts; it must be approved while they are still fresh.
    proposal_ttl_hours: float = 24.0
    today: Callable[[], date] = field(default=utc_date, repr=False)
    model_mode: str = "deterministic"
    avalai_api_key: str = field(default="", repr=False, compare=False)

    def __post_init__(self):
        url = urlsplit(self.backend_url)
        if url.scheme not in ("http", "https") or not url.netloc or url.query or url.fragment or url.username:
            raise ValueError("invalid backend URL")
        if self.read_attempts not in (1, 2) or self.publish_attempts not in (1, 2):
            raise ValueError("attempts must be bounded to one or two")
        if self.mcp_timeout <= 0 or self.http_timeout <= 0 or self.proposal_ttl_hours <= 0:
            raise ValueError("timeouts must be positive")
        if self.model_mode not in ("deterministic", "avalai"):
            raise ValueError("CONTEXTPILOT_MODEL_MODE must be deterministic or avalai")
        if self.model_mode == "avalai" and not self.avalai_api_key.strip():
            raise ValueError("AVALAI_API_KEY is required when CONTEXTPILOT_MODEL_MODE=avalai")
        object.__setattr__(self, "backend_url", self.backend_url.rstrip("/"))
        object.__setattr__(self, "database", Path(self.database).resolve())
        object.__setattr__(self, "mcp_server", Path(self.mcp_server).resolve())

    @classmethod
    def from_env(cls):
        def local_path(name, default):
            path = Path(os.environ.get(name, default))
            return path if path.is_absolute() else ROOT / path
        mode = os.environ.get("CONTEXTPILOT_MODEL_MODE", "deterministic").strip()
        settings = dict(
            backend_url=os.environ.get("CONTEXTPILOT_API_URL", "http://127.0.0.1:8765"),
            database=local_path("CONTEXTPILOT_DB", "var/assistant.sqlite3"),
            trace_path=local_path("CONTEXTPILOT_TRACE", "var/traces.jsonl"),
            mcp_server=local_path("CONTEXTPILOT_MCP_SERVER", "mcp_server.py"),
            model_mode=mode,
            avalai_api_key=os.environ.get("AVALAI_API_KEY", "").strip() if mode == "avalai" else "",
        )
        # Demo/evaluation only: pin the policy clock (YYYY-MM-DD) so date-dependent
        # answers are reproducible. An invalid value fails at startup, not mid-request.
        pinned = os.environ.get("CONTEXTPILOT_TODAY", "").strip()
        if pinned:
            fixed = date.fromisoformat(pinned)
            settings["today"] = lambda: fixed
        return cls(**settings)

