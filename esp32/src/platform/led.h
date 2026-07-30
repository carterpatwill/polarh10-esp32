#pragma once

// Single status LED (GPIO21, active-low). update() reflects the whole-system
// state; set() is a raw override used while blocking on a WiFi join.
namespace platform::led {

void begin();          // board power-on + LED pin
void set(bool on);     // raw drive (true = lit)
void update();         // pick the blink pattern from current system state

}  // namespace platform::led
