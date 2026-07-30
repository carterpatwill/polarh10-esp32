#include "platform/usb_host.h"
#include <Arduino.h>
#include "soc/soc.h"                    // REG_READ
#include "soc/usb_serial_jtag_reg.h"    // USB-Serial-JTAG SOF frame counter

namespace platform::usb {

// USB host presence via the USB-Serial-JTAG SOF frame counter.
//
// `(bool)Serial` only reflects DTR — set when a program *opens* the port — so a
// plain plug-in with no serial monitor never tripped sync (and a dumb charger
// looked the same as a laptop). The SOF frame index instead advances every 1 ms
// whenever a real USB host has enumerated the board, and stays frozen on battery
// or a dumb charger. We sample it and treat "advancing" as host-present.
static inline uint32_t sofFrame() {
    return REG_READ(USB_SERIAL_JTAG_FRAM_NUM_REG) & USB_SERIAL_JTAG_SOF_FRAME_INDEX;
}

bool hostPresent() {
    static uint32_t prev = 0, lastChange = 0, lastSample = 0;
    static bool     seeded = false, present = false;
    uint32_t now = millis();
    if (now - lastSample >= 120) {
        lastSample = now;
        uint32_t f = sofFrame();
        if (seeded && f != prev) { lastChange = now; present = true; }  // frames moving → host
        prev = f; seeded = true;
    }
    if (present && now - lastChange > 1500) present = false;            // frozen → no host
    return present;
}

}  // namespace platform::usb
