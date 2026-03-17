"""
Hardware driver wrapper for the Waveshare 3.6inch e-Paper HAT+ (E).

Module: waveshare_epd.epd_3in6e
Resolution: 600x400, 6 colours (black white yellow red blue green)
Refresh: ~15 s full refresh

Falls back to simulation mode if the Waveshare library is not installed
so the service starts and the API works regardless of hardware availability.
"""

import logging
import threading

from PIL import Image

logger = logging.getLogger(__name__)

# ── palette for quantising PIL images to the 6-colour e-ink set ───────────────
# Order matches the Spectra 6 encoding used by epd_3in6e.getbuffer()
EINK_COLORS = {
    "black":  (0,   0,   0),
    "white":  (255, 255, 255),
    "green":  (30,  150, 50),
    "blue":   (30,  100, 200),
    "red":    (200, 30,  30),
    "yellow": (255, 215, 0),
}

_PALETTE_FLAT = [c for rgb in EINK_COLORS.values() for c in rgb]
_PALETTE_FLAT += [0] * (768 - len(_PALETTE_FLAT))

WIDTH  = 600
HEIGHT = 400

_lock = threading.Lock()   # SPI bus is not thread-safe
_epd  = None
_sim  = False


def _init_epd():
    global _epd, _sim
    try:
        from waveshare_epd import epd_3in6e   # noqa: PLC0415
        _epd = epd_3in6e.EPD()
        _epd.init()
        logger.info("EPD hardware ready (%d×%d)", _epd.width, _epd.height)
    except Exception as exc:
        logger.warning("EPD unavailable (%s) — simulation mode", exc)
        _sim = True


def quantize(img: Image.Image) -> Image.Image:
    """Quantize an RGB image to the 6-colour e-ink palette (no dither)."""
    palette = Image.new("P", (1, 1))
    palette.putpalette(_PALETTE_FLAT)
    return img.convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE)


def show(image: Image.Image) -> None:
    """Push a PIL image to the display. Blocking (~15 s). Thread-safe."""
    with _lock:
        if _sim:
            logger.info("[SIM] display updated (%d×%d)", image.width, image.height)
            return
        global _epd
        if _epd is None:
            _init_epd()
        if _sim:
            return
        try:
            _epd.init()
            _epd.display(_epd.getbuffer(quantize(image.rotate(180))))
            _epd.sleep()
        except Exception as exc:
            logger.error("EPD show error: %s", exc)


def clear() -> None:
    """Clear display to white."""
    with _lock:
        if _sim:
            logger.info("[SIM] display cleared")
            return
        global _epd
        if _epd is None:
            _init_epd()
        if _sim:
            return
        try:
            _epd.init()
            _epd.Clear()
            _epd.sleep()
        except Exception as exc:
            logger.error("EPD clear error: %s", exc)


def is_simulation() -> bool:
    return _sim


# Initialise at import time so the first show() call doesn't block the event loop
_init_epd()
