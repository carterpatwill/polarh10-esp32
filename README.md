# ESP32 Polar H10 HR Receiver

Receives heart rate data from a Polar H10 over BLE and POSTs it to a Raspberry Pi server over WiFi.

## Hardware

- LilyGo T-Display-S3
- Polar H10 heart rate monitor
- Raspberry Pi (receiver)

## Project Structure

```
esp32/                  — PlatformIO project (flash to ESP32)
Raspberrypi/
  hr_receiver/          — Python receiver that runs on the Pi
    hr_receiver.py
    install.sh
    requirements.txt
    hr_receiver.service
```

## Setup

### 1. Configure credentials

Edit `esp32/src/config.h`:

```c
#define HOME_SSID   "your wifi name"
#define HOME_PASS   "your wifi password"
```

The eduroam credentials are already set as the fallback network.

### 2. Set up the Pi receiver (one time)

SSH into your Pi and run:

```bash
cd Raspberrypi/hr_receiver
bash install.sh
```

The script installs dependencies and optionally sets up a systemd service so the receiver starts automatically on boot.

### 3. Flash the ESP32

Build and upload via PlatformIO from the `esp32/` directory.

## Running

**Every time, in this order:**

**Step 1 — Start the receiver on the Pi** (skip if systemd service is enabled):
```bash
cd Raspberrypi/hr_receiver
source .venv/bin/activate
python hr_receiver.py
```

**Step 2 — Power on the ESP32.**

The ESP32 finds the Pi automatically via mDNS (`hr-server.local`) — no IP configuration needed.

To check the systemd service:
```bash
sudo systemctl status hr_receiver
sudo journalctl -u hr_receiver -f
```

## Status indicators (backlight)

| Backlight | Meaning |
|---|---|
| On solid | Booting |
| Slow blink | Connecting to WiFi |
| Quick blinks → on | WiFi connected |
| Slow blink | Scanning for Polar H10 |
| Off | Connected to Polar, recording |

## Data

HR readings are saved to `Raspberrypi/hr_receiver/hr_data.db` (SQLite) with timestamp, BPM, and RR intervals.

To copy the database off the Pi:
```bash
scp pi@pi4server.local:~/projects/python/esp-polar/hr_receiver/hr_data.db data/
```
