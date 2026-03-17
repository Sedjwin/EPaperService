"""Shared in-memory display state."""
import time
from dataclasses import dataclass, field


@dataclass
class HistoryEntry:
    id: str
    timestamp: float
    mode: str          # "stats" | "agent" | "upload" | "drawing"
    agent: str | None
    description: str   # human-readable label


@dataclass
class DisplayState:
    mode: str = "idle"           # "idle" | "agent"
    agent_name: str | None = None
    content: dict | None = None
    expires_at: float | None = None
    last_updated: float = field(default_factory=time.time)
    current_image_png: bytes | None = None   # latest display frame as PNG bytes
    history: list = field(default_factory=list)  # list[HistoryEntry], newest last

    def is_expired(self) -> bool:
        return self.expires_at is not None and time.time() > self.expires_at

    def release(self):
        self.mode = "idle"
        self.agent_name = None
        self.content = None
        self.expires_at = None


state = DisplayState()
