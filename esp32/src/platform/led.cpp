#include "platform/led.h"
#include "ble/polar.h"
#include "record/recorder.h"
#include "sync/sync_mode.h"
#include "config.h"
#include <Arduino.h>
#include <WiFi.h>

namespace platform::led {

// GPIO21, active-low (LOW = lit). See config.h for the state legend, and
// docs/led-blink-patterns.md for the full pattern reference ('.' = on, '_' = off).
static constexpr int PIN_BL = PIN_STATUS_LED;

void begin() {
    if (PIN_POWER_ON >= 0) {           // peripheral power-enable (unused on XIAO)
        pinMode(PIN_POWER_ON, OUTPUT);
        digitalWrite(PIN_POWER_ON, HIGH);
    }
    pinMode(PIN_BL, OUTPUT);
    digitalWrite(PIN_BL, HIGH);         // off
}

void set(bool on) {
    digitalWrite(PIN_BL, on ? LOW : HIGH);
}

namespace {

// Repeating patterns are boolean frames played one slot at a time (true = lit).
constexpr uint32_t SLOT_MS = 130;

// Looking for Polar: three blinks then a pause  →  ...___
const bool SCAN_POLAR[] = {1, 0, 1, 0, 1, 0, 0, 0, 0, 0};
// Looking for WiFi (USB plugged in): one blink then a long gap  →  .____
const bool SCAN_WIFI[]  = {1, 0, 0, 0, 0, 0, 0, 0};

// Play a repeating frame pattern at SLOT_MS per frame. Switching to a different
// pattern restarts it cleanly from frame 0.
void playLoop(const bool* frames, size_t n) {
    static const bool* cur      = nullptr;
    static uint32_t    lastTick = 0;
    static size_t      idx      = 0;
    uint32_t now = millis();

    if (frames != cur) {                 // pattern changed → restart
        cur = frames; idx = 0; lastTick = now; set(frames[0]);
        return;
    }
    if (now - lastTick >= SLOT_MS) {
        lastTick = now;
        idx = (idx + 1) % n;
        set(frames[idx]);
    }
}

}  // namespace

// Drive the LED to reflect the current state. Non-blocking; called every loop.
//   Record mode, no strap   → ...___ scan blink (looking for Polar)
//   Record mode, strap found → 8 fast blinks once, then LED off while recording
//   Record mode, flash full  → continuous fast blink (distinct from the scan)
//   Sync mode, no WiFi yet   → .____ blink (looking for WiFi)
//   Sync mode, WiFi joined   → solid on
void update() {
    uint32_t now = millis();
    Mode mode = syncmode::current();

    // --- Sync mode (USB plugged in) ---
    if (mode == Mode::Sync) {
        if (WiFi.status() == WL_CONNECTED) { set(true); return; }   // WiFi found → solid
        playLoop(SCAN_WIFI, sizeof(SCAN_WIFI));                      // searching → .____
        return;
    }

    // --- Record mode ---
    // Flash full takes precedence: continuous fast blink so it can't be
    // mistaken for the "looking for Polar" scan pattern.
    if (record::isFlashFull()) {
        static uint32_t ffTick = 0;
        static bool     ffOn   = false;
        if (now - ffTick >= 100) { ffTick = now; ffOn = !ffOn; set(ffOn); }
        return;
    }

    static bool     wasConnected = false;
    static bool     bursting     = false;
    static uint32_t burstTick    = 0;
    static uint8_t  burstStep    = 0;

    bool connected = ble::isConnected();

    // Rising edge: the strap just connected → play the 8-blink "found it" burst.
    if (connected && !wasConnected) {
        bursting = true; burstStep = 0; burstTick = now; set(true);
    }
    wasConnected = connected;

    if (!connected) { playLoop(SCAN_POLAR, sizeof(SCAN_POLAR)); return; }

    // Connected. 8 fast blinks (16 half-steps @ 80 ms), then the LED goes dark
    // and stays off while recording quietly.
    if (bursting) {
        if (now - burstTick >= 80) {
            burstTick = now;
            if (++burstStep >= 16) { bursting = false; set(false); return; }
            set(burstStep % 2 == 0);
        }
        return;
    }
    set(false);   // recording quietly → LED off
}

}  // namespace platform::led
