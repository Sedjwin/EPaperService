"""Idle screen configuration: widget selection, quotes, apply-now settings."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

QUOTES: list[str] = [
    "Work hard, stay humble, die anyway.",
    "Every day is a gift. Return policy: none.",
    "You miss 100% of the shots you don't take — same with the shots you do.",
    "Be the change you wish to see. Then blame someone else when it breaks.",
    "Dream big. Fail bigger.",
    "Today is the first day of the rest of your problems.",
    "If at first you don't succeed, redefine success.",
    "Believe in yourself. No one else will.",
    "Your only limit is your WiFi signal.",
    "Live. Laugh. Eventually accept mortality.",
    "Chase your dreams. Then take a nap.",
    "You are enough. But more RAM would help.",
    "Be yourself. Unless you can be Batman. Always be Batman.",
    "The secret to success is pretending you know what you're doing.",
    "Good things come to those who wait. So does death.",
    "Rise and grind. Or just lie there. The sun doesn't care.",
    "You can't pour from an empty cup. Fill yours with caffeine.",
    "Stay positive. Or at least stay indoors.",
    "Every expert was once a beginner. Every beginner eventually gives up.",
    "Do what you love. Pay for it later.",
    "Life is short. Eat the cake. Regret the cake. Repeat.",
    "Success is a journey, not a destination. Also the motorway is closed.",
    "Think outside the box. The box is still there though.",
    "Be the person your dog thinks you are. Your dog has very low standards.",
    "The best time to plant a tree was 20 years ago. So was the second best.",
    "Hustle until your haters ask if you're hiring. Then say no.",
    "Go the extra mile. No one's there because it's far.",
    "You are a diamond in the rough. Mostly rough.",
    "Don't stop when you're tired. Stop when you're done. Then cry.",
    "Everything happens for a reason. The reason is usually physics.",
    "You got this. Whatever 'this' is. It's probably fine.",
    "Hard work beats talent. Unless talent works hard. And you're tired.",
    "Happiness is a choice. So is staying in bed.",
    "The grind never stops. Neither does the crippling self-doubt.",
    "Fall seven times, stand up eight. See a doctor about that.",
    "One day or day one. Both involve considerable suffering.",
    "Be a voice, not an echo. Unless you're wrong. Then be very quiet.",
    "Strive for progress, not perfection. Perfection is unachievable anyway.",
    "You were not born to just pay bills and die. But it's looking that way.",
    "Make your dreams so big they scare you. Then they scare everyone else too.",
    "Difficult roads lead to beautiful destinations. Except the A34.",
    "Push yourself, because no one else is going to do it for you. Probably.",
    "The pain you feel today is the strength you feel tomorrow. Or an injury.",
    "Opportunities don't happen, you create them. Mostly by accident.",
    "Great things never come from comfort zones. Or sandwiches. Sandwiches do.",
    "It always seems impossible until it's done. Then it seems obvious in hindsight.",
    "You don't have to be great to start. But you do have to start. Eventually.",
    "Wake up with determination. Go to bed with satisfaction. Nap in between.",
    "The secret of getting ahead is getting started. The secret of getting started is unclear.",
    "Don't wait for opportunity. Create it. Or at least leave a note.",
]

ALL_WIDGETS: list[str] = [
    "cpu", "ram", "temp", "disk",
    "weather", "uptime", "ip",
    "active_services", "next_booking", "booking_count",
    "custom_text", "quote",
]

WIDGET_LABELS: dict[str, str] = {
    "cpu":           "CPU usage",
    "ram":           "RAM usage",
    "temp":          "Temperature",
    "disk":          "Disk usage",
    "weather":       "Weather",
    "uptime":        "Uptime",
    "ip":            "IP / hostname",
    "active_services": "Active services",
    "next_booking":  "Next booking",
    "booking_count": "Booking count",
    "custom_text":   "Custom text",
    "quote":         "Inspirational quote™",
}

DEFAULT_CONFIG: dict = {
    "mode": "auto",           # "auto" | "markdown" | "svg" | "image"
    "auto_widgets": ["cpu", "ram", "temp", "disk", "weather", "next_booking", "quote"],
    "weather_location": "Abingdon, UK",
    "custom_text": "",
    "quote_index": 0,
    "services_to_check": [
        {"name": "Dashboard",    "url": "http://localhost:8000/health"},
        {"name": "AIGateway",    "url": "http://localhost:8001/health"},
        {"name": "VoiceService", "url": "http://localhost:8002/health"},
        {"name": "AgentManager", "url": "http://localhost:8003/health"},
        {"name": "UserManager",  "url": "http://localhost:8005/health"},
    ],
    "content_type": "markdown",
    "content": "",
    "image_b64": "",
    "apply_now_mode":       "snap",   # "fixed" | "snap" | "retain"
    "apply_now_fixed_mins": 30,
    "apply_now_snap_mins":  20,
    "idle_interval_mins":   20,
}

_config_path: Path | None = None


def init(data_dir: Path) -> None:
    global _config_path
    _config_path = data_dir / "idle_config.json"


def load() -> dict:
    if _config_path and _config_path.exists():
        try:
            saved = json.loads(_config_path.read_text())
            merged = dict(DEFAULT_CONFIG)
            merged.update(saved)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save(cfg: dict) -> None:
    if _config_path:
        _config_path.write_text(json.dumps(cfg, indent=2))


def next_quote(cfg: dict) -> str:
    """Return the next quote and advance the index (persists to disk)."""
    idx = int(cfg.get("quote_index", 0)) % len(QUOTES)
    cfg["quote_index"] = (idx + 1) % len(QUOTES)
    save(cfg)
    return QUOTES[idx]


def compute_end_time(cfg: dict, start: datetime) -> datetime:
    """Compute apply-now end time from config mode."""
    mode = cfg.get("apply_now_mode", "snap")
    if mode == "fixed":
        return start + timedelta(minutes=int(cfg.get("apply_now_fixed_mins", 30)))
    elif mode == "snap":
        snap = int(cfg.get("apply_now_snap_mins", 20))
        total = start.hour * 60 + start.minute
        next_snap = math.ceil((total + 1) / snap) * snap
        if next_snap >= 1440:
            return (start.replace(hour=0, minute=0, second=0, microsecond=0)
                    + timedelta(days=1))
        return start.replace(
            hour=next_snap // 60, minute=next_snap % 60,
            second=0, microsecond=0,
        )
    else:  # retain — far future, displaced only by a real booking
        return start + timedelta(days=3650)
