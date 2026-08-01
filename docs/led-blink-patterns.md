# Status LED patterns

Two LEDs, driven by `esp32/src/platform/led.cpp`:

- **User light** — onboard LED on **GPIO21**, **active-low** (driving the pin
  `LOW` lights it). The WiFi/sync indicator.
- **BLE light** — external LED on **GPIO1 (D0)**, **active-high** (driving the
  pin `HIGH` lights it). The Polar-strap connection indicator.

Notation: `.` = LED on, `_` = LED off. Patterns loop unless noted.

## BLE light (GPIO1) — Polar strap

| State | Pattern | Meaning |
|-------|---------|---------|
| **Looking for Polar** (BLE scan) | `._._._` | Even 250 ms blink — scanning/connecting. Default idle state on battery. |
| **Polar found** | solid ~2 s then off | Held solid for `BLE_CONNECT_HOLD_MS` (2 s) to confirm the connection, then dark while recording quietly. |
| **Disconnected** | → back to *Looking for Polar* | Any drop of the BLE link returns to the blink. |
| **Sync mode** (USB plugged in) | off | BLE isn't active while syncing. |

## User light (GPIO21) — WiFi / sync

| State | Pattern | Meaning |
|-------|---------|---------|
| **On battery** (record mode) | off | Nothing plugged in — WiFi indicator is dark. |
| **Plugged in, looking for WiFi** | solid on | Held solid from the moment sync mode starts searching. |
| **WiFi found** | solid on | Stays held solid — connected, ready to upload. |
| **Unplugged from USB** | off, → back to record | Leaving sync mode turns the user light off; the next plug-in re-searches for WiFi. |
| **Flash full** (record mode) | `.___.___` fast blink | The LittleFS partition is full and recording has stopped — the user light borrows a continuous 100 ms toggle as an alarm (nothing else uses it on battery). |

## State flow

```
        power on (on battery)
           │
           ▼
   ┌──────────────────┐   Polar found     ┌──────────────────────┐
   │ Looking for Polar │ ────────────────▶ │ Connected             │
   │  BLE: ._._._      │  BLE: solid 2 s   │  BLE: off (recording) │
   └──────────────────┘  → off            └──────────────────────┘
        ▲   │                                     │
        │   │ USB plugged in                      │ BLE disconnect
        │   ▼                                     │
        │  ┌──────────────────┐  WiFi found  ┌────┴───────────────┐
        │  │ Looking for WiFi  │ ───────────▶ │ WiFi connected     │
        │  │ user: solid on    │              │ user: solid on     │
        │  └──────────────────┘              └────────────────────┘
        │           │                                │
        └───────────┴────── USB unplugged ───────────┘
                    (user light off, BLE scan resumes)
```

## Implementation

Implemented in `led::update()` (`esp32/src/platform/led.cpp`), called every loop
and fully non-blocking. The BLE light blinks at a 250 ms toggle while scanning;
on the BLE rising edge it holds solid for `BLE_CONNECT_HOLD_MS` (`config.h`) then
goes dark. The user light is held solid the whole time the board is in sync mode
(USB plugged in), and stays dark on battery unless the flash fills up.
