# EPaperService

Controls a Waveshare 3.6" e-Paper HAT+ (E) 6-colour display (600×400 px) on the Raspberry Pi 5. Two operating modes: **idle** (auto-refreshes a configurable stats/widget screen) and **booked** (displays content pushed via the booking schedule). Includes a full browser-based admin panel.

**Ports:** `8004` (internal) · `13375` (external HTTPS via Caddy)

---

## Overview

- **Idle mode** — renders a widget dashboard (CPU, RAM, temp, weather, quotes, etc.) at a configurable interval
- **Booking mode** — any authenticated principal (human or agent) can reserve a time slot and push markdown, SVG, or image content
- **Scheduling** — conflict-detected calendar with start/end times; bookings activate and expire automatically
- **Idle screen config** — fully configurable via UI or API; changes take effect immediately
- **Admin panel** — browser UI at `/` for bookings, history, idle screen config, and live preview
- **Simulation mode** — falls back gracefully if GPIO/SPI hardware is unavailable

---

## Configuration

Set via environment variables or `.env`:

| Variable            | Default           | Description                              |
|---------------------|-------------------|------------------------------------------|
| `HOST`              | `127.0.0.1`       | Listen address                           |
| `PORT`              | `8004`            | Listen port                              |
| `SECRET_KEY`        | *(required)*      | JWT signing key (shared with UserManager)|
| `USERMANAGER_URL`   | `http://localhost:8005` | UserManager base URL             |

Persistent idle screen settings are stored in `data/idle_config.json` (auto-created on first run).

---

## Authentication

Endpoints marked **Auth** accept:

- **JWT Bearer** — `Authorization: Bearer <token>` (issued by UserManager `/auth/login`)
- **API key** — `X-API-Key: <key>` (issued by AgentManager for agent principals)

Both are validated against UserManager and produce the same unified `principal` object. The booking owner and admin-only cancel rules apply to both.

---

## API Reference

### Status & Preview

| Method | Path           | Auth | Description                                    |
|--------|----------------|------|------------------------------------------------|
| GET    | `/api/status`  | None | Current mode, active booking, last updated     |
| GET    | `/api/preview` | None | Current display frame as PNG (`204` if none)   |

**`/api/status` response:**
```json
{
  "mode": "idle",
  "current_booking": null,
  "last_updated": 1710676800.0,
  "simulation": false
}
```

`mode` is `"idle"` or `"booked"`. `current_booking` is a full booking object or `null`.

---

### Bookings

#### Create a booking

| Method | Path       | Auth | Description                        |
|--------|------------|------|------------------------------------|
| POST   | `/api/book`| Yes  | Reserve a display time slot        |

**Request:**
```json
{
  "content_type": "markdown",
  "content": "# Hello\n\nThis is my slot.",
  "start_time": "2026-03-22T14:00:00Z",
  "end_time":   "2026-03-22T14:30:00Z",
  "description": "My booking"
}
```

| Field          | Type   | Required | Description                                                    |
|----------------|--------|----------|----------------------------------------------------------------|
| `content_type` | string | Yes      | `markdown`, `svg`, or `image`                                  |
| `content`      | string | Yes      | Markdown/SVG text, or base64-encoded PNG for `image`           |
| `start_time`   | string | No       | ISO 8601 UTC. Defaults to **now**                              |
| `end_time`     | string | No       | ISO 8601 UTC. Defaults to computed from **apply-now config**   |
| `description`  | string | No       | Human-readable label for the calendar                          |

Returns `201` with the created booking object, or `409` with conflict details.

**Apply-now modes** (used when `end_time` is omitted):

| Mode      | Behaviour                                                             |
|-----------|-----------------------------------------------------------------------|
| `fixed`   | `start + N minutes` (configurable, default 30)                       |
| `snap`    | Next X-minute clock boundary, e.g. :00/:20/:40 (default, 20 min)    |
| `retain`  | Far future — slot holds until displaced by a later booking           |

#### Cancel a booking

| Method | Path                    | Auth | Description                             |
|--------|-------------------------|------|-----------------------------------------|
| DELETE | `/api/book/{booking_id}`| Yes  | Cancel a booking (owner or admin only)  |

#### Schedule & History

| Method | Path                              | Auth | Description                                 |
|--------|-----------------------------------|------|---------------------------------------------|
| GET    | `/api/schedule`                   | None | Active + upcoming bookings (not cancelled)  |
| GET    | `/api/history`                    | None | All bookings newest-first (`limit`, `offset` params) |
| GET    | `/api/history/{booking_id}/image` | None | PNG rendered for a past booking             |

**Booking object:**
```json
{
  "booking_id":     "06293a6c-...",
  "principal_id":   1,
  "principal_name": "ATLAS",
  "principal_type": "agent",
  "content_type":   "markdown",
  "start_time":     "2026-03-22T14:00:00Z",
  "end_time":       "2026-03-22T14:30:00Z",
  "description":    "Status update",
  "cancelled":      false,
  "created_at":     "2026-03-22T13:55:00Z"
}
```

---

### Idle Screen Config

| Method | Path                | Auth | Description                                          |
|--------|---------------------|------|------------------------------------------------------|
| GET    | `/api/idle-config`  | None | Return current idle config (image replaced with sentinel) |
| POST   | `/api/idle-config`  | Yes  | Save config and **immediately** refresh the display  |
| POST   | `/api/idle-preview` | None | Render a preview PNG for any config (no auth needed) |

**Config fields:**

| Field                  | Default                          | Description                                        |
|------------------------|----------------------------------|----------------------------------------------------|
| `mode`                 | `"auto"`                         | `auto`, `markdown`, `svg`, or `image`              |
| `auto_widgets`         | `["cpu","ram","temp","disk","weather","next_booking","quote"]` | Ordered list of active widgets |
| `weather_location`     | `"Abingdon, UK"`                 | Location string for wttr.in                        |
| `custom_text`          | `""`                             | Text shown by the `custom_text` widget             |
| `services_to_check`    | *(5 local services)*             | Array of `{name, url}` for service health checks   |
| `content`              | `""`                             | Markdown or SVG content (for non-auto modes)       |
| `image_b64`            | `""`                             | Base64 PNG (for image mode). GET returns `"__has_image__"` if set |
| `apply_now_mode`       | `"snap"`                         | `fixed`, `snap`, or `retain`                       |
| `apply_now_fixed_mins` | `30`                             | Duration for `fixed` mode                          |
| `apply_now_snap_mins`  | `20`                             | Snap interval in minutes                           |
| `idle_interval_mins`   | `20`                             | How often to auto-refresh the idle screen          |

**Available widgets** (listed in render order):
`cpu`, `ram`, `temp`, `disk`, `weather`, `uptime`, `ip`, `active_services`, `next_booking`, `booking_count`, `custom_text`, `quote`

The `quote` widget fills all remaining vertical space below other widgets, auto-sizing its font to maximise the text. 50 rotating parody inspirational quotes; font style rotates across Dancing Script, Kaushan Script, Kristi, and Lobster.

**`/api/idle-preview` request:** any valid config dict (or `{}` to preview the saved config).
Returns `image/png` at 600×400.

---

### Auth Proxy

| Method | Path               | Auth | Description                                   |
|--------|--------------------|------|-----------------------------------------------|
| POST   | `/api/auth/login`  | None | Proxies login to UserManager (for admin panel) |

---

### Health

| Method | Path      | Auth | Description          |
|--------|-----------|------|----------------------|
| GET    | `/health` | None | Service health check |

```json
{ "status": "ok", "service": "EPaperService", "simulation": false, "display_mode": "idle", "current_booking": null }
```

---

## Admin Panel

Served at `/` (static `admin.html`).

| Tab              | Features                                                                 |
|------------------|--------------------------------------------------------------------------|
| **Dashboard**    | Live display preview, current status, active booking                     |
| **Schedule**     | 7-day calendar view of upcoming bookings                                 |
| **Book Slot**    | Create a booking with live 600×400 canvas preview for all content types; drag-drop/paste image upload |
| **History**      | All bookings with inline rendered PNG thumbnails; Apply Again dialog to re-book with custom or apply-now timing |
| **Idle Screen**  | Widget checklist with up/down reorder, weather location, custom text, service list, server-rendered live preview; apply-now mode settings |

---

## Display Loop

The service polls every 30 seconds:

1. If an active booking exists and has changed → render and push it
2. If a booking just expired → revert to idle immediately
3. If idle and `idle_interval_mins` has elapsed → refresh stats

Saving idle config via API or admin panel triggers an immediate refresh outside the loop.

---

## Display Hardware

- **Device**: Waveshare 3.6" e-Paper HAT+ (E)
- **Resolution**: 600 × 400 px
- **Colours**: 6 (black, white, red, green, blue, yellow — Spectra 6 palette)
- **Interface**: SPI via Raspberry Pi GPIO
- **Refresh time**: ~15 s (full refresh)

`simulation=true` is set automatically if `/dev/spidev` is unavailable; the service renders to memory only.

---

## Running

```bash
cd EPaperService
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8004
```

Requires SPI/GPIO access for hardware mode. Run via systemd for production:

```bash
sudo systemctl start epaperservice.service
```
