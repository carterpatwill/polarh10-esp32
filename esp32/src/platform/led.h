#pragma once

// Two status LEDs: the onboard user light (GPIO21, active-low) as the WiFi/sync
// indicator, and an external BLE light (GPIO1, active-high) for the Polar strap.
// update() reflects the whole-system state; set() is a raw override of the user
// light used while blocking on a WiFi join.
namespace platform::led {

void begin();          // board power-on + both LED pins
void set(bool on);     // raw drive of the user light (true = lit)
void bleSet(bool on);  // raw drive of the BLE light (true = lit)
void update();         // pick the blink pattern for both LEDs from system state

}  // namespace platform::led
