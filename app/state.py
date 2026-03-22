"""Shared in-memory display state."""
import time
from dataclasses import dataclass, field


@dataclass
class DisplayState:
    mode: str = "idle"                   # "idle" | "booked"
    current_booking_id: str | None = None
    agent_name: str | None = None        # name of current booking's principal
    last_updated: float = field(default_factory=time.time)
    current_image_png: bytes | None = None  # latest display frame as PNG bytes


state = DisplayState()
