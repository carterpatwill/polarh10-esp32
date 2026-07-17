# ESP32 Polar H10 HR Receiver

Real-time heart rate logging from a Polar H10 worn on the field, relayed through an ESP32 over WiFi to a Raspberry Pi at home. Built for football practice — eventual goal is to pair HR data with accelerometer data to get recovery and exertion insights.

```
Polar H10 ──BLE──► ESP32 (on body) ──WiFi──► Raspberry Pi (at home)
```

---

## Hardware

| Part | Details | Link |
|---|---|---|
| **ESP32** | LilyGo T-Display-S3 | [Buy](https://lilygo.cc/en-us/products/t-display-s3?srsltid=AfmBOorAvhIBYAfBdJZUqVd6M9FYRvfz8ES51SflbB1JjWLCmOi4rh36) |
| **Heart rate sensor** | Polar H10 | |
| **Battery** | LiPo battery | [Buy](https://www.amazon.com/dp/B0FZSYM9T2?ref=ppx_yo2ov_dt_b_fed_asin_title&th=1) |
| **Server** | Raspberry Pi 1GB | |

<img src="PolarH10.png" width="300" alt="Polar H10"/>
<img src="RasberryPi-1G.jpg" width="300" alt="Raspberry Pi 1GB"/>

---

## Project Structure

```
esp32/                      — PlatformIO project (flashed to the ESP32)
  src/
    main.cpp
    config.h                — WiFi credentials and tuning (edit this)
Raspberrypi/
  hr_receiver/              — Python server that runs on the Pi
    hr_receiver.py
    install.sh              — one-time setup script
    requirements.txt
    hr_receiver.service     — systemd unit (auto-start on boot)
data/
  hr_data.db                — SQLite database (copied from Pi)
  analyze.py                — analysis script
```

---

## Setup

### Step 1 — Configure WiFi credentials on the ESP32

Edit `esp32/src/config.h`:

```c
#define HOME_SSID   "your wifi name"
#define HOME_PASS   "your wifi password"
```

There is also an eduroam (WPA2-Enterprise) fallback — fill in those fields if you want it to work on campus.

### Step 2 — Flash the ESP32

Open the `esp32/` folder in PlatformIO and click **Upload**, or run:

```bash
cd esp32
pio run --target upload
```

### Step 3 — Set up the Raspberry Pi receiver

SSH into your Pi:

```bash
ssh carter@pi4server.local
```

Clone or copy the project, then run the install script:

```bash
cd ~/projects/python/esp-polar/hr_receiver
bash install.sh
```

This will:
1. Create a Python virtual environment
2. Install dependencies (`zeroconf`, etc.)
3. Optionally install a systemd service so the receiver starts automatically on boot

---

## Running

### On the Pi

If you set up the systemd service, it starts automatically — nothing to do.

To start manually:

```bash
cd ~/projects/python/esp-polar/hr_receiver
source .venv/bin/activate
python hr_receiver.py
```

Check service status:

```bash
sudo systemctl status hr_receiver
```

Follow live logs:

```bash
sudo journalctl -u hr_receiver -f
```

Stop the service:

```bash
sudo systemctl stop hr_receiver
```

### On the ESP32

Power it on. It will:
1. Connect to WiFi (tries home network first, falls back to eduroam)
2. Find the Pi automatically via mDNS (`hr-server.local`) — no IP config needed
3. Scan for the Polar H10 over BLE
4. Stream HR data to the Pi in batches every 30 seconds

---

## Status Indicators (backlight)

| Backlight | Meaning |
|---|---|
| On solid | Booting |
| Slow blink | Connecting to WiFi |
| Quick blinks → on | WiFi connected |
| Slow blink | Scanning for Polar H10 |
| Off | Connected to Polar, recording |

---

## Captive Portal

The ESP32 also runs an open WiFi access point (`ESP32-Polar`). Connect to it from any phone or laptop to see a live status page showing battery, BLE connection, current BPM, and receiver status.

---

## Data

HR readings are saved to `hr_data.db` (SQLite) on the Pi with:
- `received` — timestamp
- `t_ms` — millis since ESP32 boot
- `bpm` — heart rate
- `rr_ms` — RR intervals (JSON array, ms)

### Copy data from the Pi to your Mac

Run from your Mac terminal (not SSH):

```bash
scp carter@pi4server.local:~/projects/python/esp-polar/hr_receiver/hr_data.db data/
```

### Analyze

```bash
cd data
source ../.venv/bin/activate
python analyze.py
```

Prints key metrics (avg/min/max BPM, RR intervals, duration) and shows a BPM-over-time graph.
