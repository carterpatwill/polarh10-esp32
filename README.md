# ESP32 Polar H10 HR Receiver

Receives heart rate data from a Polar H10 over BLE and POSTs it to a laptop server over WiFi.

## Hardware

- LilyGo T-Display-S3
- Polar H10 heart rate monitor

## Setup

### 1. Configure credentials

Edit `src/config.h`:

```c
#define HOME_SSID   "your wifi name"
#define HOME_PASS   "your wifi password"
```

The eduroam credentials are already set as the fallback network.

### 2. Install Python dependencies (one time)

```bash
cd esp32-polar
python3 -m venv .venv
.venv/bin/pip install zeroconf
```

### 3. Flash the ESP32

Build and upload via PlatformIO.

## Running

**Every time, in this order:**

**Step 1 — Start the receiver on your laptop:**
```bash
.venv/bin/python hr_receiver.py
```

**Step 2 — Power on the ESP32.**

The ESP32 finds the laptop automatically via mDNS (`hr-server.local`) — no IP configuration needed.

## Status indicators (backlight)

| Backlight | Meaning |
|---|---|
| On solid | Booting |
| Slow blink | Connecting to WiFi |
| Quick blinks → on | WiFi connected |
| Slow blink | Scanning for Polar H10 |
| Off | Connected to Polar, recording |

## Data

HR readings are saved to `hr_data.db` (SQLite) with timestamp, BPM, and RR intervals.
