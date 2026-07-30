#pragma once

// Battery gauge via a 1:2 divider on PIN_BAT_ADC. Returns -1 when no divider is
// wired (PIN_BAT_ADC < 0), else 0–100 percent.
namespace platform::battery {

int readPercent();

}  // namespace platform::battery
