# Status LED blink patterns

Single onboard status LED on **GPIO21**, **active-low** (driving the pin `LOW`
lights it). Driven by `esp32/src/platform/led.cpp`.

Notation: `.` = LED on (blink), `_` = LED off. Patterns loop unless noted.

## Patterns

| State | Pattern | Meaning |
|-------|---------|---------|
| **Looking for Polar** (BLE scan) | `...___...___` | Three quick blinks, three-slot pause, repeat. This is the default idle/searching state. |
| **Polar found** | `........` then off | Eight fast blinks, then the LED goes dark — connection established, now recording quietly. |
| **Disconnected** | → back to *Looking for Polar* | Any drop of the BLE link returns to the `...___` scan pattern. |
| **Looking for WiFi** (USB plugged in) | `.____.___.___` | One blink then a long off gap, repeated — sync mode searching for WiFi. |
| **WiFi found** (USB plugged in) | solid on | LED held steady on — connected, ready to upload. |
| **Unplugged from USB** | → back to *Looking for Polar* | Leaving sync mode returns to the BLE scan pattern (and the 8-blink burst again once the Polar is found). |

## State flow

```
        power on
           │
           ▼
   ┌──────────────────┐   Polar found    ┌──────────────────┐
   │ Looking for Polar │ ───────────────▶ │ Connected         │
   │  ...___...___     │  ........ → off  │  (LED off, record)│
   └──────────────────┘                  └──────────────────┘
        ▲   │                                     │
        │   │ USB plugged in                      │ BLE disconnect
        │   ▼                                     │
        │  ┌──────────────────┐  WiFi found  ┌────┴──────────┐
        │  │ Looking for WiFi  │ ───────────▶ │ WiFi connected│
        │  │  .____.___.___    │              │  solid on     │
        │  └──────────────────┘              └───────────────┘
        │           │                                │
        └───────────┴────── USB unplugged ───────────┘
```

## Implementation

Implemented in `led::update()` (`esp32/src/platform/led.cpp`), called every loop
and fully non-blocking. Repeating patterns are boolean frame arrays played at
130 ms/frame (`SCAN_POLAR`, `SCAN_WIFI`); the 8-blink "found it" burst fires once
on the BLE rising edge, then the LED stays dark while recording.

**Extra state not in the table:** *flash full* while recording. The LittleFS
partition is full and recording has stopped — shown as a **continuous fast
blink** (100 ms toggle), deliberately distinct from the `...___` scan so a full
disk can't be mistaken for a normal search.
