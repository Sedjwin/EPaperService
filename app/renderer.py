"""
PIL-based renderer for the 600×400 e-ink display.

All functions return an RGB Image ready to pass to display.show().
Colours are chosen from the 6-colour Spectra 6 palette.
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

# Cursive/script fonts for quote widget — rotated by quote index
_QUOTE_FONT_PATHS = [
    "/usr/share/fonts/opentype/dancingscript/DancingScript-Bold.otf",   # bouncy cursive
    "/usr/share/fonts/opentype/kaushanscript/KaushanScript-Regular.otf", # brushed ink
    "/usr/share/fonts/truetype/kristi/Kristi.ttf",                       # felt-tip casual
    "/usr/share/fonts/opentype/lobster/lobster.otf",                      # bold decorative
]

def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _quote_font(size: int, quote_index: int) -> ImageFont.FreeTypeFont:
    path = _QUOTE_FONT_PATHS[quote_index % len(_QUOTE_FONT_PATHS)]
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return _font(size)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0]
    except Exception:
        return len(text) * (font.size // 2)


# ── shared idle header ────────────────────────────────────────────────────────

def _draw_idle_header(draw: ImageDraw.ImageDraw) -> int:
    """Draw the standard idle header (hostname + timestamp). Returns y after header."""
    draw.rectangle([0, 0, WIDTH, 50], fill=FG)
    draw.text((16, 10), "chip.iampc.uk", font=_font(26), fill=BG)
    ts = datetime.now().strftime("%a %d %b  %H:%M")
    draw.text((WIDTH - 290, 10), ts, font=_font(26), fill=BG)
    return 60


# ── bar helper ────────────────────────────────────────────────────────────────

def _bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int,
         pct: float, fill: tuple, label: str, value_str: str) -> None:
    label_w = 80
    bar_x = x + label_w
    bar_w = w - label_w - 120
    draw.text((x, y + 2), label, font=_font(20), fill=FG)
    draw.rectangle([bar_x, y + 6, bar_x + bar_w, y + h - 6], fill=(220, 220, 220))
    filled_w = max(2, int(bar_w * min(pct, 1.0)))
    draw.rectangle([bar_x, y + 6, bar_x + filled_w, y + h - 6], fill=fill)
    draw.text((bar_x + bar_w + 8, y + 2), value_str, font=_font(20), fill=FG)


# ── text widget ───────────────────────────────────────────────────────────────

def _widget_text(draw: ImageDraw.ImageDraw, y: int, label: str, value: str,
                 value_color: tuple = None) -> int:
    """Render a single label: value line. Returns new y."""
    vc = value_color or FG
    draw.text((20, y), label, font=_font(18), fill=(120, 120, 120))
    draw.text((110, y), value, font=_font(18), fill=vc)
    return y + 26


# ── weather widget ────────────────────────────────────────────────────────────

def _widget_weather(draw: ImageDraw.ImageDraw, y: int, weather: dict | None) -> int:
    """Render weather. Returns new y."""
    if not weather:
        return _widget_text(draw, y, "WEATHER", "unavailable", (160, 160, 160))
    temp = weather.get("temp_c", "?")
    cond = weather.get("condition", "")
    loc  = weather.get("location", "")

    # Temperature colour
    try:
        t = int(temp)
        if t <= 5:   tc = EINK_COLORS["blue"]
        elif t <= 18: tc = FG
        elif t <= 26: tc = EINK_COLORS["yellow"]
        else:         tc = EINK_COLORS["red"]
    except Exception:
        tc = FG

    draw.text((20, y),      "WEATHER", font=_font(18), fill=(120, 120, 120))
    draw.text((110, y),     f"{temp}°C", font=_font(22), fill=tc)
    tw = _text_w(draw, f"{temp}°C", _font(22)) + 8
    draw.text((110 + tw, y + 3), cond, font=_font(18), fill=FG)
    if loc:
        draw.text((110, y + 24), loc, font=_font(14), fill=(160, 160, 160))
    return y + 48


# ── services widget ───────────────────────────────────────────────────────────

def _widget_services(draw: ImageDraw.ImageDraw, y: int, services: list[dict]) -> int:
    """Render service health as coloured dots + names. Returns new y."""
    draw.text((20, y), "SERVICES", font=_font(18), fill=(120, 120, 120))
    x = 110
    for svc in services:
        color = EINK_COLORS["green"] if svc.get("up") else EINK_COLORS["red"]
        draw.ellipse([x, y + 4, x + 12, y + 16], fill=color)
        x += 16
        name = svc["name"]
        draw.text((x, y), name, font=_font(15), fill=FG)
        x += _text_w(draw, name, _font(15)) + 10
        if x > WIDTH - 80:
            break
    return y + 28


# ── quote widget ──────────────────────────────────────────────────────────────

def _wrap_pixels(draw: ImageDraw.ImageDraw, text: str, font,
                 max_w: int) -> list[str]:
    """Word-wrap text using actual pixel measurements."""
    words = text.split()
    lines, current, current_w = [], [], 0
    space_w = _text_w(draw, " ", font)
    for word in words:
        ww = _text_w(draw, word, font)
        if current and current_w + space_w + ww > max_w:
            lines.append(" ".join(current))
            current, current_w = [word], ww
        else:
            current_w = ww if not current else current_w + space_w + ww
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _widget_quote(draw: ImageDraw.ImageDraw, y_start: int, quote: str,
                  available_h: int, quote_index: int = 0) -> None:
    """Render a quote filling the available space using a rotating script font.

    Binary-searches for the largest font size where the pixel-wrapped text
    fits within available_h, so short quotes render large and long quotes
    shrink to fit rather than being cut off.
    """
    if available_h < 20 or not quote:
        return

    margin = 30
    max_w  = WIDTH - 2 * margin

    def measure(size: int):
        font   = _quote_font(size, quote_index)
        lines  = _wrap_pixels(draw, quote, font, max_w)
        bb     = draw.textbbox((0, 0), "Ag", font=font)
        line_h = (bb[3] - bb[1]) + max(4, size // 5)
        return font, lines, line_h, len(lines) * line_h

    # Binary search: largest size whose total height fits in available_h
    lo, hi, best = 10, 90, None
    while lo <= hi:
        mid = (lo + hi) // 2
        font, lines, line_h, total_h = measure(mid)
        if total_h <= available_h - 8:
            best = (mid, font, lines, line_h, total_h)
            lo = mid + 1
        else:
            hi = mid - 1

    if best is None:
        _, font, lines, line_h, total_h = measure(10)
    else:
        _, font, lines, line_h, total_h = best

    sy = y_start + (available_h - total_h) // 2
    for line in lines:
        lw = _text_w(draw, line, font)
        draw.text(((WIDTH - lw) // 2, sy), line, font=font, fill=(80, 80, 80))
        sy += line_h


# ── AUTO idle renderer ────────────────────────────────────────────────────────

def _render_idle_auto(cfg: dict, ext: dict) -> Image.Image:
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    y    = _draw_idle_header(draw)

    widgets  = cfg.get("auto_widgets", ["cpu", "ram", "temp"])
    non_quote = [w for w in widgets if w != "quote"]
    has_quote = "quote" in widgets

    for w in non_quote:
        if y > HEIGHT - 40:
            break

        if w == "cpu":
            cpu = psutil.cpu_percent(interval=0.3)
            _bar(draw, 20, y, WIDTH - 40, 38, cpu / 100,
                 EINK_COLORS["blue"], "CPU", f"{cpu:.0f}%")
            y += 48

        elif w == "ram":
            mem = psutil.virtual_memory()
            used = mem.used / (1024 ** 3); total = mem.total / (1024 ** 3)
            _bar(draw, 20, y, WIDTH - 40, 38, mem.percent / 100,
                 EINK_COLORS["green"], "RAM", f"{used:.1f}/{total:.0f}G")
            y += 48

        elif w == "temp":
            temp = None
            try:
                temps = psutil.sensors_temperatures()
                for name in ("cpu_thermal", "rp1_adc", "bcm2835_thermal", "coretemp"):
                    if name in temps and temps[name]:
                        temp = temps[name][0].current; break
                if temp is None:
                    first = next(iter(temps.values()), None)
                    if first: temp = first[0].current
            except Exception:
                pass
            if temp is not None:
                pct = (temp - 30) / 50
                tc  = EINK_COLORS["red"] if temp > 70 else EINK_COLORS["yellow"]
                _bar(draw, 20, y, WIDTH - 40, 38, pct, tc, "TEMP", f"{temp:.0f}°C")
                y += 48

        elif w == "disk":
            try:
                disk = psutil.disk_usage("/")
                used = disk.used / (1024 ** 3); total = disk.total / (1024 ** 3)
                _bar(draw, 20, y, WIDTH - 40, 38, disk.percent / 100,
                     EINK_COLORS["yellow"], "DISK", f"{used:.0f}/{total:.0f}G")
                y += 48
            except Exception:
                pass

        elif w == "weather":
            y = _widget_weather(draw, y, ext.get("weather"))

        elif w == "uptime":
            try:
                import time
                secs = int(time.time() - psutil.boot_time())
                d, r = divmod(secs, 86400); h, r = divmod(r, 3600); m = r // 60
                upstr = (f"{d}d " if d else "") + f"{h}h {m}m"
            except Exception:
                upstr = "?"
            y = _widget_text(draw, y, "UPTIME", upstr)

        elif w == "ip":
            try:
                import socket
                host = socket.gethostname()
                ip   = socket.gethostbyname(host)
                y = _widget_text(draw, y, "HOST", f"{host}  ·  {ip}")
            except Exception:
                pass

        elif w == "active_services":
            svcs = ext.get("services")
            if svcs:
                y = _widget_services(draw, y, svcs)

        elif w == "next_booking":
            nb = ext.get("next_booking")
            if nb:
                name  = nb.get("principal_name", "")
                start = nb.get("start_time")
                ts    = start.strftime("%d %b %H:%M") if start else "?"
                desc  = nb.get("description") or name
                y = _widget_text(draw, y, "NEXT", f"{ts}  {desc[:24]}")
            else:
                y = _widget_text(draw, y, "NEXT", "no upcoming bookings",
                                 (160, 160, 160))

        elif w == "booking_count":
            count = ext.get("booking_count", 0)
            y = _widget_text(draw, y, "BOOKINGS",
                             f"{count} upcoming" if count != 1 else "1 upcoming")

        elif w == "custom_text":
            txt = cfg.get("custom_text", "").strip()
            if txt:
                y = _widget_text(draw, y, "·", txt)

    # Quote fills remaining space
    if has_quote and ext.get("quote"):
        available = HEIGHT - y - 6
        _widget_quote(draw, y, ext["quote"], available,
                      quote_index=cfg.get("quote_index", 0))

    return img


# ── Idle content / image modes ────────────────────────────────────────────────

def _render_idle_text_content(content: str) -> Image.Image:
    """Markdown content with idle header."""
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    y    = _draw_idle_header(draw)

    for raw_line in content.splitlines():
        if y >= HEIGHT - 20:
            break
        line  = raw_line.rstrip()
        if not line:
            y += 8; continue
        clean = (line.replace("**", "").replace("__", "")
                     .replace("*", "").replace("_", ""))
        if line.startswith("### "):
            draw.text((16, y), clean[4:], font=_font(20), fill=FG); y += 28
        elif line.startswith("## "):
            draw.text((16, y), clean[3:], font=_font(24), fill=FG); y += 32
        elif line.startswith("# "):
            draw.text((16, y), clean[2:], font=_font(28), fill=FG); y += 38
        elif line.startswith(("- ", "* ", "+ ")):
            text = clean[2:]
            for i, wrapped in enumerate(textwrap.wrap(text, width=46)):
                if y >= HEIGHT - 20: break
                draw.text((16, y), ("•  " if i == 0 else "   ") + wrapped,
                          font=_font(18), fill=FG)
                y += 24
        else:
            for wrapped in textwrap.wrap(clean, width=52):
                if y >= HEIGHT - 20: break
                draw.text((16, y), wrapped, font=_font(18), fill=FG); y += 24
    return img


# ── Main render_stats entry point ─────────────────────────────────────────────

def render_stats(config: dict | None = None,
                 extra:  dict | None = None) -> Image.Image:
    """Render the idle screen. config from idle_config; extra has pre-fetched data."""
    cfg = config or {}
    ext = extra  or {}
    mode = cfg.get("mode", "auto")

    if mode == "markdown" and cfg.get("content"):
        return _render_idle_text_content(cfg["content"])
    elif mode == "svg" and cfg.get("content"):
        return render_svg(cfg["content"], "", datetime.now(), datetime.now())
    elif mode == "image" and cfg.get("image_b64"):
        return render_image(cfg["image_b64"], "", datetime.now(), datetime.now())
    else:
        return _render_idle_auto(cfg, ext)


# ── Booking renderers (unchanged) ─────────────────────────────────────────────

def render_message(agent: str, title: str, body: str,
                   color: str = "blue", duration: int | None = None) -> Image.Image:
    accent = ACCENT_COLORS.get(color, EINK_COLORS["blue"])
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, WIDTH, 54], fill=accent)
    draw.text((16, 10), agent, font=_font(28), fill=BG)
    ts = datetime.now().strftime("%H:%M")
    draw.text((WIDTH - 80, 14), ts, font=_font(24), fill=BG)
    y = 72
    if title:
        draw.text((20, y), title, font=_font(30), fill=FG); y += 44
    if body:
        for line in textwrap.wrap(body, width=40):
            draw.text((20, y), line, font=_font(22), fill=FG); y += 30
            if y > HEIGHT - 60: break
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
    draw.rectangle([0, 0, WIDTH, 50], fill=FG)
    draw.text((16, 10), principal_name, font=_font(26), fill=BG)
    slot = f"{start_time.strftime('%d %b  %H:%M')} – {end_time.strftime('%H:%M')}"
    draw.text((WIDTH - 280, 14), slot, font=_font(20), fill=BG)
    return 60


def render_markdown(content: str, principal_name: str,
                    start_time: datetime, end_time: datetime) -> Image.Image:
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    y    = render_booking_header(draw, principal_name, start_time, end_time)
    for raw_line in content.splitlines():
        if y >= HEIGHT - 20: break
        line  = raw_line.rstrip()
        if not line: y += 8; continue
        clean = (line.replace("**", "").replace("__", "")
                     .replace("*", "").replace("_", ""))
        if line.startswith("### "):
            draw.text((16, y), clean[4:], font=_font(20), fill=FG); y += 28
        elif line.startswith("## "):
            draw.text((16, y), clean[3:], font=_font(24), fill=FG); y += 32
        elif line.startswith("# "):
            draw.text((16, y), clean[2:], font=_font(28), fill=FG); y += 38
        elif line.startswith(("- ", "* ", "+ ")):
            text = clean[2:]
            for i, wrapped in enumerate(textwrap.wrap(text, width=46)):
                if y >= HEIGHT - 20: break
                draw.text((16, y), ("•  " if i == 0 else "   ") + wrapped,
                          font=_font(18), fill=FG)
                y += 24
        else:
            for wrapped in textwrap.wrap(clean, width=52):
                if y >= HEIGHT - 20: break
                draw.text((16, y), wrapped, font=_font(18), fill=FG); y += 24
    return img


def render_svg(content: str, principal_name: str,
               start_time: datetime, end_time: datetime) -> Image.Image:
    try:
        import cairosvg
        png_bytes = cairosvg.svg2png(
            bytestring=content.encode(),
            output_width=WIDTH, output_height=HEIGHT,
        )
        return Image.open(io.BytesIO(png_bytes)).convert("RGB").resize(
            (WIDTH, HEIGHT), Image.LANCZOS)
    except ImportError:
        return render_markdown(
            "# SVG not supported\n\ncairosvg is not installed.",
            principal_name, start_time, end_time)
    except Exception as exc:
        return render_markdown(f"# SVG render error\n\n{exc}",
                               principal_name, start_time, end_time)


def render_image(content_b64: str, principal_name: str,
                 start_time: datetime, end_time: datetime) -> Image.Image:
    try:
        png_bytes = base64.b64decode(content_b64)
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        return img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    except Exception as exc:
        return render_markdown(f"# Image render error\n\n{exc}",
                               principal_name, start_time, end_time)


def render_list(agent: str, title: str, items: list[str],
                color: str = "blue", duration: int | None = None) -> Image.Image:
    accent = ACCENT_COLORS.get(color, EINK_COLORS["blue"])
    img  = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, WIDTH, 54], fill=accent)
    draw.text((16, 10), agent, font=_font(28), fill=BG)
    ts = datetime.now().strftime("%H:%M")
    draw.text((WIDTH - 80, 14), ts, font=_font(24), fill=BG)
    y = 72
    if title:
        draw.text((20, y), title, font=_font(26), fill=FG); y += 40
    for item in items:
        if y > HEIGHT - 60: break
        for i, line in enumerate(textwrap.wrap(item, width=40)):
            prefix = "•  " if i == 0 else "   "
            draw.text((20, y), prefix + line, font=_font(22), fill=FG); y += 28
    draw.rectangle([0, HEIGHT - 40, WIDTH, HEIGHT], fill=(240, 240, 240))
    foot = ("agent control  ·  permanent" if not duration
            else f"agent control  ·  {duration // 60} min")
    draw.text((20, HEIGHT - 30), foot, font=_font(18), fill=(80, 80, 80))
    return img
