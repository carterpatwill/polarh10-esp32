#include "platform/led.h"
#include "ble/polar.h"
#include "record/recorder.h"
#include "sync/sync_mode.h"
#include "config.h"
#include <Arduino.h>
#include <WiFi.h>

namespace platform::led {

// Two LEDs (see docs/led-blink-patterns.md, '.' = on / '_' = off):
//   user light — GPIO21, active-low  → WiFi/sync indicator
//   BLE  light — GPIO1,  active-high → Polar-strap connection indicator
static constexpr int PIN_USER = PIN_STATUS_LED;
static constexpr int PIN_BLE  = PIN_BLE_LED;

void begin() {
    if (PIN_POWER_ON >= 0) {           // peripheral power-enable (unused on XIAO)
        pinMode(PIN_POWER_ON, OUTPUT);
        digitalWrite(PIN_POWER_ON, HIGH);
    }
    pinMode(PIN_USER, OUTPUT);
    digitalWrite(PIN_USER, STATUS_LED_OFF);
    pinMode(PIN_BLE, OUTPUT);
    digitalWrite(PIN_BLE, BLE_LED_OFF);
}

// Raw drive of the user light (GPIO21). Used while blocking on a WiFi join.
void set(bool on) {
    digitalWrite(PIN_USER, on ? STATUS_LED_ON : STATUS_LED_OFF);
}

// Raw drive of the BLE light (GPIO1). Used to force it off when entering sync.
void bleSet(bool on) {
    digitalWrite(PIN_BLE, on ? BLE_LED_ON : BLE_LED_OFF);
}

namespace {

void setBle(bool on) { bleSet(on); }

// Even 50/50 blink of the BLE LED while scanning for the strap.
void bleBlink() {
    static uint32_t tick = 0;
    static bool     on   = false;
    uint32_t now = millis();
    if (now - tick >= 250) { tick = now; on = !on; setBle(on); }
}

}  // namespace

// Drive both LEDs to reflect the current state. Non-blocking; called every loop.
//   User light (WiFi):
//     Sync mode (USB plugged in) → solid on the whole time, until unplugged.
//     Record mode (on battery)   → off (or fast blink if the flash is full).
//   BLE light (Polar strap):
//     Scanning                   → blink.
//     Just connected             → solid for BLE_CONNECT_HOLD_MS, then off.
//     Recording / not scanning   → off.
void update() {
    uint32_t now = millis();
    Mode mode = syncmode::current();

    // ── Sync mode (USB plugged in) ────────────────────────────────────────────
    if (mode == Mode::Sync) {
        setBle(false);                              // BLE LED off while syncing
        if (WiFi.status() == WL_CONNECTED) {
            set(true);                              // WiFi found → user light solid
        } else {
            static uint32_t wTick = 0;
            static bool     wOn   = false;
            if (now - wTick >= 250) { wTick = now; wOn = !wOn; set(wOn); }  // searching → flash
        }
        return;
    }

    // ── Record mode (on battery) ──────────────────────────────────────────────
    // User light is the WiFi indicator only, so it stays dark on battery —
    // except a full flash, which borrows it as a continuous fast-blink alarm.
    if (record::isFlashFull()) {
        static uint32_t ffTick = 0;
        static bool     ffOn   = false;
        if (now - ffTick >= 100) { ffTick = now; ffOn = !ffOn; set(ffOn); }
        setBle(false);
        return;
    }
    set(false);

    // BLE light: blink while scanning; on connect hold solid for a moment, then
    // go dark for the rest of the recording.
    static bool     wasConnected = false;
    static bool     holding      = false;
    static uint32_t holdUntil    = 0;

    bool connected = ble::isConnected();
    if (connected && !wasConnected) { holding = true; holdUntil = now + BLE_CONNECT_HOLD_MS; }
    if (!connected)                 { holding = false; }
    wasConnected = connected;

    if (!connected) {
        bleBlink();
    } else if (holding && (int32_t)(holdUntil - now) > 0) {
        setBle(true);                 // just connected → solid confirmation
    } else {
        holding = false;
        setBle(false);                // connected & confirmed → off while recording
    }
}

}  // namespace platform::led
