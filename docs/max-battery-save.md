# Max Battery-Save Mode — BLE-only recording, WiFi dark until dump

A design spec for the lowest-power operating mode of the ESP32-Polar recorder.
The goal: **record a full session with the WiFi radio completely off**, buffering
everything in PSRAM, and only powering WiFi up **once at the end** to upload the
whole session in one burst.

> This is the *maximum savings* variant. It trades away live streaming and
> website start/stop control (see [Session control](#session-control)) in
> exchange for roughly halving average current draw.

---

## Why this saves so much

The expensive part of WiFi is **not** sending data — it's keeping the radio
*associated and awake*. Your ACC batches are only a few KB; the cost is that the
WiFi modem sits powered-on 100% of the time. Fully powering it off
(`WiFi.mode(WIFI_OFF)` / `esp_wifi_stop()`) deletes that idle cost **and** frees
the shared radio so BLE coexistence overhead drops too.

The one radio you **cannot** turn off is **BLE** — it has to stay connected to
the Polar H10 to keep receiving HR + accelerometer notifications. But a BLE
connection is cheap (~single-digit to ~15 mA) compared to always-on WiFi.

| State | Radios up | Approx. average draw |
|-------|-----------|----------------------|
| Current firmware (continuous WiFi, 2 s publishes) | WiFi + BLE | ~120 mA |
| **This mode (recording)** | **BLE only** | **~30–50 mA (~40 mA)** |
| This mode (end-of-session dump, brief) | WiFi + BLE | ~150–300 mA for ~1–3 min |

The dump spike is a rounding error on the total energy budget
(~200 mA × ~2 min ≈ 6 mAh).

---

## Session lifecycle

```
        ┌─────────────────────────────────────────────────────────┐
        │                                                         │
   idle ├── start ──► RECORDING ──── stop ──► DUMPING ── done ──► idle
        │            (WiFi OFF,               (WiFi ON,          │
        │             BLE on,                  chunked MQTT      │
        │             buffer → PSRAM)          upload, then      │
        │                                      WiFi OFF)         │
        └─────────────────────────────────────────────────────────┘
```

1. **idle** — waiting to start. WiFi may be on here so the site can still see the
   device / kick off a session.
2. **RECORDING** — WiFi powered fully off. BLE stays connected to the H10. Every
   HR + ACC sample is appended to a ring buffer in PSRAM. No network traffic.
3. **DUMPING** — WiFi powers on, reconnects to the last-known-good network + MQTT,
   uploads the entire buffer in numbered chunks, then powers WiFi back off.
4. Back to **idle**.

### Session control

Because WiFi is **off during RECORDING, the ESP cannot receive an MQTT `stop`
command mid-session.** Session control therefore needs an offline trigger:

- **Recommended:** the onboard **BOOT button (GPIO0)** on the XIAO ESP32-S3 —
  press to start, press again to stop.
  - Gotcha: GPIO0 held low *at boot* enters flash-download mode. Only read it as a
    button *after* startup, and debounce it.
- Alternative: **fixed timer** — start (button/MQTT) then auto-stop after a
  configured duration.

> If you want to keep **website start/stop**, this max-save mode is not the right
> one — see `docs/` for the modem-sleep variant, which keeps WiFi associated
> (in `WIFI_PS_MAX_MODEM`) so the site stays in control at a higher (~60–85 mA)
> draw.

---

## Data buffering (PSRAM)

The XIAO ESP32-S3 has **8 MB PSRAM**; realistically ~6–7 MB is usable after the
framework/heap take their share. Data is tiny, so this is plenty.

### Packed sample format

Drop the redundant per-sample timestamp (the sample rate is fixed and known) and
store one timestamp per batch, reconstructing per-sample time on the receiver.

| Stream | Packed size | Rate | Bytes/hour |
|--------|-------------|------|-----------|
| ACC (x,y,z int16) | 6 B/sample | 25 Hz | ~0.54 MB |
| HR (bpm + RR) | ~small | ~1/s | negligible |
| **Total** | | | **~0.55 MB/hr** |

### Storage-limited runtime

| Usable PSRAM | Max session length (packed) |
|--------------|-----------------------------|
| 6 MB | ~11 h |
| 7 MB | ~13 h |

(Current unpacked `ACCSample` at ~12 B → ~1.2 MB/hr → PSRAM caps at only ~5 h,
which is why packing is worth it.)

> ⚠️ **Volatility:** PSRAM is lost on power failure / crash / reset. In this mode
> a dead battery mid-session loses the whole recording. If that risk is
> unacceptable, buffer to **LittleFS (flash)** instead — survives reboots at the
> cost of flash wear and a little write power.

---

## End-of-session dump

You **cannot** `mqtt.publish()` a multi-MB blob in one call — PubSubClient is
capped (`setBufferSize(8192)`). The dump must **chunk** the buffer:

- Split the buffer into sequential chunks that fit the MQTT buffer.
- Tag each chunk with a **sequence number + total count** so the Pi can reassemble
  and detect gaps.
- Send a small **manifest** first (sample rate, range, sample count, session
  label, chunk count) and an **end marker** last.
- Retry a chunk on publish failure; abort the WiFi-off transition until the dump
  confirms complete.

---

## Power budget & battery sizing

Average recording draw ~40 mA (range ~30–50 mA), 80% usable LiPo capacity.

| Runtime target | Consumed | Rated capacity needed |
|----------------|----------|-----------------------|
| 2 h | ~80 mAh | **~100 mAh** |
| 3 h | ~120 mAh | **~150 mAh** |
| 5 h | ~200 mAh | ~250 mAh |

**Recommended cell for a 2–3 h target: a 250 mAh single-cell LiPo.**
- 150 mAh also covers 2–3 h but with tighter margins.
- The 250 mAh gives ~5 h headroom **and** handles the end-of-session dump spike
  (~200–300 mA) without voltage sag — a 150 mAh cell at 1C is only ~150 mA
  continuous, so the larger cell is safer for the burst.

Connect to the XIAO's **BAT+ / BAT−** pads (onboard charger + regulator).

---

## Firmware implementation checklist

- [ ] Add a **session state machine** (`idle` / `recording` / `dumping`).
- [ ] Add **BOOT-button** start/stop (debounced, read only after boot).
- [ ] On `start`: `WiFi.mode(WIFI_OFF)` / `esp_wifi_stop()`; begin PSRAM buffering.
- [ ] Allocate a **PSRAM ring buffer** (`ps_malloc` / `heap_caps_malloc(..,
      MALLOC_CAP_SPIRAM)`); write packed HR + ACC records from the BLE callbacks.
- [ ] Cache the **last-known-good WiFi network + credentials** so the dump
      reconnects fast instead of walking the 4-network fallback chain.
- [ ] On `stop`: bring WiFi up, reconnect MQTT, run the **chunked dump**, confirm,
      then `WiFi.mode(WIFI_OFF)` again.
- [ ] `setCpuFrequencyMhz(160)` at boot (WiFi needs ≥80 MHz; 160 is the safe
      sweet spot with BLE coexistence).
- [ ] Stop holding the status LED solid during recording (saves a few mA; blink
      briefly on state changes instead).
- [ ] Config knobs: sample rate, buffer size cap, chunk size, buffer target
      (PSRAM vs LittleFS), timer duration (if using the timer option).

---

## Caveats

- All draw figures are **estimates** — dual-radio coexistence makes real numbers
  hard to predict. Measure with an inline power meter before/after.
- **No live data** during recording — the Pi/site sees nothing until the dump.
- **Crash/power loss loses the session** unless you buffer to flash.
- Reconnecting WiFi for the dump repeats association + TLS handshake (~1–3 s);
  caching the winning network keeps this fast (eduroam/WPA2-EAP is the slow one).
