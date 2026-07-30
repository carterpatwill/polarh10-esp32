#include "platform/led.h"
#include "ble/polar.h"
#include "record/recorder.h"
#include "sync/sync_mode.h"
#include "config.h"
#include <Arduino.h>

namespace platform::led {

// GPIO21, active-low (LOW = lit). See config.h for the state legend.
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

// Drive the LED to reflect the current state. Non-blocking; called every loop.
//   solid            → recording (strap connected), or sync upload done
//   slow blink       → scanning / no strap
//   fast blink       → sync mode (searching WiFi / uploading)
//   rapid triple     → flash full, recording stopped
void update() {
    static uint32_t lastTick = 0;
    static uint8_t  phase    = 0;
    uint32_t now = millis();

    Mode mode = syncmode::current();

    if (record::isFlashFull() && mode == Mode::Record) {
        // Three quick blinks, then a pause: pattern repeats every ~1.2 s.
        if (now - lastTick >= 120) {
            lastTick = now;
            phase = (phase + 1) % 10;               // 6 blink-halves + 4 pause slots
            digitalWrite(PIN_BL, (phase < 6 && (phase % 2 == 0)) ? LOW : HIGH);
        }
        return;
    }

    if (mode == Mode::Record && ble::isConnected()) { set(true); return; }  // solid
    if (mode == Mode::Sync && syncmode::uploadDone())   { set(true); return; }  // upload done → solid

    uint32_t period = (mode == Mode::Sync) ? 150 : 700;         // fast vs slow blink
    if (now - lastTick >= period) {
        lastTick = now;
        phase ^= 1;
        digitalWrite(PIN_BL, phase ? LOW : HIGH);
    }
}

}  // namespace platform::led
