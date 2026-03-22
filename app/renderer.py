"""
PIL-based renderer for the 600×400 e-ink display.

All functions return an RGB Image ready to pass to display.show().
Colours are chosen from the 6-colour Spectra 6 palette so quantisation
is lossless for UI elements.
"""

import base64
import io
import textwrap
from datetime import datetime

import psutil
from PIL import Image, ImageDraw, ImageFont

from .display import EINK_COLORS, WIDTH, HEIGHT

# ── colours ───────────────────────────────────────────────────────────────────
BG      = EINK_COLORS["white"]
FG      = EINK_COLORS["black"]
ACCENT_COLORS = {
    "blue":   EINK_COLORS["blue"],
    "green":  EINK_COLORS["green"],
    "red":    EINK_COLORS["red"],
    "yellow": EINK_COLORS["yellow"],
    "black":  EINK_COLORS["black"],
}

# ── fonts ─────────────────────────────────────────────────────────────────────
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int,
         pct: float, fill: tuple, label: str, value_str: str) -> None:
    """Draw a labelled progress bar."""
    label_w = 80
    bar_x = x + label_w
    bar_w = w - label_w - 120
    draw.text((x, y + 2), label, font=_font(20), fill=FG)
    # Track
    draw.rectangle([bar_x, y + 6, bar_x + bar_w, y + h - 6], fill=(220, 220, 220))
    # Fill
    filled_w = max(2, int(bar_w * min(pct, 1.0)))
    draw.rectangle([bar_x, y + 6, bar_x + filled_w, y + h - 6], fill=fill)
    # Value
    draw.text((bar_x + bar_w + 8, y + 2), value_str, font=_font(20), fill=FG)


def render_stats() -> Image.Image:
    """Render idle stats: time, CPU, RAM, temperature."""
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # ── header ────────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, WIDTH, 50], fill=FG)
    draw.text((16, 10), "chip.iampc.uk", font=_font(26), fill=BG)
    ts = datetime.now().strftime("%a %d %b  %H:%M")
    draw.text((WIDTH - 290, 10), ts, font=_font(26), fill=BG)

    # ── system metrics ────────────────────────────────────────────────────────
    cpu = psutil.cpu_percent(interval=0.3)
    mem = psutil.virtual_memory()
    ram_used = mem.used  / (1024 ** 3)
    ram_total = mem.total / (1024 ** 3)

    temp = None
    try:
        temps = psutil.sensors_temperatures()
        for name in ("cpu_thermal", "rp1_adc", "bcm2835_thermal", "coretemp"):
            if name in temps and temps[name]:
                temp = temps[name][0].current
                break
        if temp is None:
            first = next(iter(temps.values()), None)
            if first:
                temp = first[0].current
    except Exception:
        pass

    y = 70
    gap = 52
    _bar(draw, 20, y,       WIDTH - 40, 40, cpu / 100,
         EINK_COLORS["blue"],  "CPU",  f"{cpu:.0f}%")
    _bar(draw, 20, y + gap, WIDTH - 40, 40, mem.percent / 100,
         EINK_COLORS["green"], "RAM",  f"{ram_used:.1f}/{ram_total:.0f} GB")

    if temp is not None:
        temp_pct = (temp - 30) / 50   # 30°C = 0%, 80°C = 100%
        temp_color = EINK_COLORS["red"] if temp > 70 else EINK_COLORS["yellow"]
        _bar(draw, 20, y + gap * 2, WIDTH - 40, 40, temp_pct,
             temp_color, "TEMP", f"{temp:.0f}°C")

    # ── divider ───────────────────────────────────────────────────────────────
    div_y = y + gap * 3 + 10
    draw.rectangle([20, div_y, WIDTH - 20, div_y + 2], fill=FG)

    # ── footer ────────────────────────────────────────────────────────────────
    updated = datetime.now().strftime("%H:%M")
    draw.text((20, div_y + 12), f"idle  ·  updated {updated}", font=_font(20), fill=(120, 120, 120))

    return img


def render_message(agent: str, title: str, body: str,
                   color: str = "blue", duration: int | None = None) -> Image.Image:
    """Render an agent message with title + wrapped body text."""
    accent = ACCENT_COLORS.get(color, EINK_COLORS["blue"])
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # ── header ────────────────────────────────────────────────────────────────
    draw.rectangle([0, 0, WIDTH, 54], fill=accent)
    draw.text((16, 10), agent, font=_font(28), fill=BG)
    ts = datetime.now().strftime("%H:%M")
    draw.text((WIDTH - 80, 14), ts, font=_font(24), fill=BG)

    # ── title ─────────────────────────────────────────────────────────────────
    y = 72
    if title:
        draw.text((20, y), title, font=_font(30), fill=FG)
        y += 44

    # ── body ──────────────────────────────────────────────────────────────────
    if body:
        for line in textwrap.wrap(body, width=40):
            draw.text((20, y), line, font=_font(22), fill=FG)
            y += 30
            if y > HEIGHT - 60:
                break

    # ── footer ────────────────────────────────────────────────────────────────
    draw.rectangle([0, HEIGHT - 40, WIDTH, HEIGHT], fill=(240, 240, 240))
    if duration:
        mins = duration // 60
        foot = f"agent control  ·  {mins} min" if mins else f"agent control  ·  {duration}s"
    else:
        foot = "agent control  ·  permanent"
    draw.text((20, HEIGHT - 30), foot, font=_font(18), fill=(80, 80, 80))

    return img


def render_booking_header(draw: ImageDraw.ImageDraw, principal_name: str,
                          start_time: datetime, end_time: datetime) -> int:
    """Draw a standard booking header bar. Returns the y position after the header."""
    draw.rectangle([0, 0, WIDTH, 50], fill=FG)
    draw.text((16, 10), principal_name, font=_font(26), fill=BG)
    slot = f"{start_time.strftime('%d %b  %H:%M')} – {end_time.strftime('%H:%M')}"
    draw.text((WIDTH - 280, 14), slot, font=_font(20), fill=BG)
    return 60


def render_markdown(content: str, principal_name: str,
                    start_time: datetime, end_time: datetime) -> Image.Image:
    """Render simple Markdown to the e-ink display.
    Supports: # headings, - bullet points, **bold** (bold marker stripped), plain paragraphs.
    """
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    y = render_booking_header(draw, principal_name, start_time, end_time)

    for raw_line in content.splitlines():
        if y >= HEIGHT - 20:
            break
        line = raw_line.rstrip()

        if not line:
            y += 8
            continue

        # Strip bold/italic markers for rendering
        clean = line.replace("**", "").replace("__", "").replace("*", "").replace("_", "")

        if line.startswith("### "):
            draw.text((16, y), clean[4:], font=_font(20), fill=FG)
            y += 28
        elif line.startswith("## "):
            draw.text((16, y), clean[3:], font=_font(24), fill=FG)
            y += 32
        elif line.startswith("# "):
            draw.text((16, y), clean[2:], font=_font(28), fill=FG)
            y += 38
        elif line.startswith(("- ", "* ", "+ ")):
            text = clean[2:]
            for i, wrapped in enumerate(textwrap.wrap(text, width=46)):
                if y >= HEIGHT - 20:
                    break
                draw.text((16, y), ("•  " if i == 0 else "   ") + wrapped, font=_font(18), fill=FG)
                y += 24
        else:
            for wrapped in textwrap.wrap(clean, width=52):
                if y >= HEIGHT - 20:
                    break
                draw.text((16, y), wrapped, font=_font(18), fill=FG)
                y += 24

    return img


def render_svg(content: str, principal_name: str,
               start_time: datetime, end_time: datetime) -> Image.Image:
    """Render an SVG string to the display. Falls back to error message if cairosvg unavailable."""
    try:
        import cairosvg
        png_bytes = cairosvg.svg2png(
            bytestring=content.encode(),
            output_width=WIDTH,
            output_height=HEIGHT,
        )
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
        return img
    except ImportError:
        return render_markdown(
            "# SVG not supported\n\ncairosvg is not installed.\n\nUse markdown or image instead.",
            principal_name, start_time, end_time,
        )
    except Exception as exc:
        return render_markdown(
            f"# SVG render error\n\n{exc}",
            principal_name, start_time, end_time,
        )


def render_image(content_b64: str, principal_name: str,
                 start_time: datetime, end_time: datetime) -> Image.Image:
    """Render a base64-encoded PNG/JPEG to the display."""
    try:
        png_bytes = base64.b64decode(content_b64)
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
        return img
    except Exception as exc:
        return render_markdown(
            f"# Image render error\n\n{exc}",
            principal_name, start_time, end_time,
        )


def render_list(agent: str, title: str, items: list[str],
                color: str = "blue", duration: int | None = None) -> Image.Image:
    """Render an agent list with bullet points."""
    accent = ACCENT_COLORS.get(color, EINK_COLORS["blue"])
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, WIDTH, 54], fill=accent)
    draw.text((16, 10), agent, font=_font(28), fill=BG)
    ts = datetime.now().strftime("%H:%M")
    draw.text((WIDTH - 80, 14), ts, font=_font(24), fill=BG)

    y = 72
    if title:
        draw.text((20, y), title, font=_font(26), fill=FG)
        y += 40

    for item in items:
        if y > HEIGHT - 60:
            break
        for i, line in enumerate(textwrap.wrap(item, width=40)):
            prefix = "•  " if i == 0 else "   "
            draw.text((20, y), prefix + line, font=_font(22), fill=FG)
            y += 28

    draw.rectangle([0, HEIGHT - 40, WIDTH, HEIGHT], fill=(240, 240, 240))
    foot = "agent control  ·  permanent" if not duration else \
           f"agent control  ·  {duration // 60} min"
    draw.text((20, HEIGHT - 30), foot, font=_font(18), fill=(80, 80, 80))

    return img
