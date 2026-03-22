# EPaperService

Controls a Waveshare 3.6" e-Paper HAT+ (E) 6-colour display (600×400 pixels) attached to the Raspberry Pi 5. Two operating modes: **idle** (auto-refreshes system stats) and **agent** (displays content pushed by an AI agent, with optional auto-expiry). Includes a full browser-based admin panel.

**Ports:** `8004` (internal) / `13375` (external, HTTPS via Caddy)

---

## Overview

- **Idle mode**: Refreshes system stats (CPU, RAM, temp, uptime) at configurable intervals (default every 20 min, aligned to :00/:20/:40)
- **Agent mode**: Any service can push a message, list, or raw image to the display; optionally expires after a duration
- **History**: Stores the last 50 display frames (metadata + PNG) on disk
- **Simulation mode**: Falls back to an in-memory renderer if GPIO/SPI hardware is unavailable
- **Admin panel**: Browser UI for manual control, history browsing, and live preview

---

## Configuration

Set via environment variables or `.env` file:

| Variable             | Default       | Description                             |
|----------------------|---------------|-----------------------------------------|
| `HOST`               | `127.0.0.1`   | Listen address (loopback by default)    |
| `PORT`               | `8004`        | Listen port                             |
| `IDLE_INTERVAL_MIN`  | `20`          | Stats refresh interval (minutes)        |

---

## API Reference

### Status

| Method | Path           | Auth | Description                             |
|--------|----------------|------|-----------------------------------------|
| GET    | `/api/status`  | None | Current display mode and state          |

**Response:**
```json
{
  "mode": "idle",
  "agent_name": null,
  "last_updated": 1710676800,
  "simulation": false,
  "next_refresh": 1710677400
}
```

| Field          | Type    | Description                                      |
|----------------|---------|--------------------------------------------------|
| `mode`         | string  | `idle` or `agent`                                |
| `agent_name`   | string  | Name of the agent that took over (nullable)      |
| `last_updated` | number  | Unix timestamp of last display update            |
| `simulation`   | bool    | True if running without physical e-ink hardware  |
| `next_refresh` | number  | Unix timestamp of next idle refresh (idle mode only) |

---

### Preview

| Method | Path            | Auth | Description                          |
|--------|-----------------|------|--------------------------------------|
| GET    | `/api/preview`  | None | Current frame as PNG image           |

Returns `204 No Content` if nothing has been displayed yet, otherwise `image/png`.

Used by the Dashboard to show a live thumbnail of the display.

---

### Display Control

#### Show message or list

| Method | Path        | Auth | Description                                  |
|--------|-------------|------|----------------------------------------------|
| POST   | `/api/show` | None | Push content to the display (agent mode)     |

**Request (`ShowRequest`):**
```json
{
  "type": "message",
  "agent": "ATLAS",
  "title": "Reminder",
  "body": "Meeting in 5 minutes.",
  "color": "blue",
  "duration": 300000
}
```

| Field      | Type    | Description                                            |
|------------|---------|--------------------------------------------------------|
| `type`     | string  | `message` (title + body) or `list` (title + lines)    |
| `agent`    | string  | Agent name shown on display                            |
| `title`    | string  | Header text                                            |
| `body`     | string  | Body text (for `message` type)                         |
| `lines`    | array   | List of strings (for `list` type)                      |
| `color`    | string  | Accent colour: `blue`, `red`, `green`, `yellow`, etc.  |
| `duration` | number  | Auto-release after N milliseconds (optional)           |

**Response:** `{ "status": "accepted" }` (non-blocking, renders async)

---

#### Display raw image

| Method | Path                   | Auth | Description                             |
|--------|------------------------|------|-----------------------------------------|
| POST   | `/api/display-image`   | None | Push a base64-encoded PNG to the display |

**Request (`DisplayImageRequest`):**
```json
{
  "image_b64": "<base64-encoded PNG>",
  "description": "Dashboard screenshot",
  "agent": "Dashboard",
  "duration": 60000
}
```

Image is resized to 600×400 before rendering.

---

#### Release / Refresh

| Method | Path            | Auth | Description                                          |
|--------|-----------------|------|------------------------------------------------------|
| POST   | `/api/release`  | None | Return to idle mode (triggers immediate stats refresh) |
| POST   | `/api/refresh`  | None | Force an idle stats refresh without changing mode    |

Both return `{ "status": "released" }` or `{ "status": "refreshing" }`.

---

### History

| Method | Path                          | Auth | Description                        |
|--------|-------------------------------|------|------------------------------------|
| GET    | `/api/history`                | None | List history entries (newest first) |
| GET    | `/api/history/{id}/image`     | None | Retrieve PNG for a history entry   |
| DELETE | `/api/history/{id}`           | None | Delete a history entry             |

**History list response:**
```json
[
  {
    "id": "2025-03-17T12-00-00",
    "timestamp": "2025-03-17T12:00:00",
    "mode": "agent",
    "agent": "ATLAS",
    "description": "Reminder: Meeting in 5 minutes."
  },
  ...
]
```

Up to 50 entries are retained. Stored as `data/history.json` + PNG files on disk.

---

### Health

| Method | Path      | Auth | Description            |
|--------|-----------|------|------------------------|
| GET    | `/health` | None | Service health check   |

**Response:**
```json
{
  "status": "ok",
  "service": "EPaperService",
  "simulation": false,
  "display_mode": "idle"
}
```

---

## Admin Panel

The web admin panel is served at `https://<host>:13375/admin` (static `admin.html`).

Features:
- Live preview of the current display (with zoom)
- Send a message or list to the display
- Upload an image to display
- Browse and restore history frames
- Force refresh / release to idle
- Status indicator (mode, last updated, next refresh)

---

## State Machine

```
          boot
            │
            ▼
     ┌─── IDLE ───┐
     │             │
  POST /api/show  POST /api/refresh
  POST /api/       │
  display-image    └──► (re-renders stats)
     │
     ▼
   AGENT
     │
     ├──► auto-expires after `duration` ms
     └──► POST /api/release
```

---

## Display Hardware

- **Device**: Waveshare 3.6" e-Paper HAT+ (E)
- **Resolution**: 600 × 400 px
- **Colours**: 6 (black, white, red, green, blue, yellow)
- **Interface**: SPI via Raspberry Pi GPIO
- **Refresh time**: ~15 seconds (full refresh)

When `simulation=true`, the service renders to memory only (useful for development without the HAT).

---

## Running

```bash
cd EPaperService
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8004
```

For full hardware support, run as a user with SPI/GPIO access (or as root). The `SIMULATION` environment variable or lack of `/dev/spidev` will automatically enable simulation mode.
