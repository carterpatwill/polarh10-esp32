#pragma once

// Detects a real USB host (laptop, not a dumb charger) via the USB-Serial-JTAG
// SOF frame counter. Drives the record ↔ sync mode switch.
namespace platform::usb {

bool hostPresent();

}  // namespace platform::usb
