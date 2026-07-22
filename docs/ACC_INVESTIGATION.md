# Accelerometer (PMD) stream fails on the XIAO ESP32-S3

**Status:** ✅ RESOLVED — 2026-07-21. Root cause: the BLE link was unencrypted.
**Date investigated:** 2026-07-21
**Board:** Seeed XIAO ESP32-S3 (`seeed_xiao_esp32s3`)

## Resolution (the fix)

The Polar H10 **only emits the PMD accelerometer stream over an encrypted BLE link.**
Heart rate is served in the clear, so it always worked; the ACC control-point
indications and data notifications were silently withheld on the unencrypted
connection. CCCD subscribes still returned success and the control point could be
read/written — the H10 just never *pushed* PMD data without encryption.

The old devkit board had evidently bonded/encrypted with the H10 at some point; the
fresh XIAO never did, which is why ACC went silent after the board swap. It was never
RF, the antenna, NimBLE, or the parsing code.

Fix (two lines in `esp32/src/main.cpp`):
- `NimBLEDevice::setSecurityAuth(true, false, true);` at BLE init (bond, Just-Works /
  no MITM, LE Secure Connections).
- `pClient->secureConnection();` right after `connect()`, before subscribing to PMD.

Verified live: after `secureConnection()` the link reports `encrypted=1`, the START
response indication arrives (`F0 02 02 00 00 01`, status=0 OK), and ACC samples stream
to MQTT (`polar/acc`).

---

## Original investigation (kept for reference)

## Summary

The Polar H10 accelerometer stream (Polar Measurement Data / PMD service) does **not**
work on the XIAO ESP32-S3, even though the **byte-identical BLE/ACC code streamed
accelerometer data fine on the previous board** (`esp32-s3-devkitc-1`).

Heart-rate notifications work perfectly on the XIAO. Only the accelerometer fails.

All accelerometer data ever recorded (July 17–20, 2026) was captured on the old
devkit board. The July 21 board swap (commit `694abaf`) switched to the XIAO, and
the `acc` table has received **0 rows** since.

## The failure, precisely

On the XIAO, when a session starts and the firmware writes `PMD_START_ACC` to the
Polar control point:

- The write is **ATT-acknowledged** by the H10 (`writeValue(..., true)` returns `true`).
- **But the H10 sends nothing back** — neither the control-point indication response
  (`onPmdControl` never fires) nor any accelerometer data notifications
  (`onAccNotify` never fires).

Meanwhile heart-rate notifications keep flowing the entire time.

## What was checked and ruled out

Investigation used a pyserial capture of the ESP32 serial log across several
instrumented firmware builds, plus `addr2line` to decode a crash backtrace.

| Hypothesis | Result |
| --- | --- |
| PMD service / characteristics not discovered | ❌ ruled out — both found, correct properties (data = notify, ctrl = indicate) |
| CCCD subscriptions failing | ❌ ruled out — both subscribe calls return OK, even when forced with write-response |
| Notification truncation from small MTU | ❌ ruled out — MTU negotiates to **232** |
| `pmdCtrlChr` null (write never sent) | ❌ ruled out — pointer is set; write is ACK'd |
| WiFi/BLE 2.4 GHz coexistence | ❌ ruled out — ACC still silent with **WiFi fully off** (BLE-only) |
| Timing race (start before CCCD committed) | ❌ ruled out — subscribe-with-response + 300 ms settle delay changed nothing |

### Gotcha found along the way

Turning WiFi off (`WiFi.mode(WIFI_OFF)`) while the MQTT/TLS socket is still open
causes a **null-pointer crash** (`InstrFetchProhibited`, PC = 0x00000000) on the
next `mqtt.loop()` — the backtrace is entirely in the lwip/mbedtls read path
(`esp_pbuf_free` → `mbedtls_ssl_read` → `PubSubClient`). If WiFi ever needs to be
disabled at runtime, tear down MQTT first: `mqtt.disconnect(); secureClient.stop();`
before `WIFI_OFF`.

## Remaining suspects (not yet ruled out)

1. **H10 stuck PMD state.** Could not cleanly power-cycle the sensor — unsnapping the
   pod from the strap doesn't cut its power (battery is in the pod, and the BLE link
   stayed connected). The H10 has no power button.
2. **XIAO-specific BLE/RF quirk** affecting the higher-bandwidth PMD stream. HR is a
   tiny 1 Hz notification and survives; the 25 Hz PMD stream (and even its one-shot
   setup indication) does not.

## Suggested next steps

1. **Verify the strap's ACC independently** — connect the H10 with a phone app
   (nRF Connect, or Polar Sensor Logger) and start the accelerometer stream.
   - Fails there too → the H10 pod is stuck/faulty.
   - Works there → the problem is XIAO-specific.
2. **Fully sleep the H10** — take it off-body and leave it ~30+ minutes so it powers
   down, then retest a fresh session.
3. **Compare against the old devkit board** (if still available) to confirm the board
   is the differentiator.

## Relevant code

- `esp32/src/main.cpp`
  - `connectToPolar()` — discovers PMD, subscribes to data (notify) + control
    (indicate), and starts the stream if a session is active.
  - `applySession()` — writes `PMD_START_ACC` on session start.
  - `onAccNotify()` / `onPmdControl()` — the two callbacks that never fire on the XIAO.
  - `PMD_START_ACC` — the start-measurement command
    (`ACC_SAMPLE_RATE` = 25 Hz, `ACC_RANGE_G` = 8, 16-bit).
