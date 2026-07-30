#include "platform/battery.h"
#include "config.h"
#include <Arduino.h>

namespace platform::battery {

int readPercent() {
    if (PIN_BAT_ADC < 0) return -1;                       // no battery divider wired
    uint32_t mv = analogReadMilliVolts(PIN_BAT_ADC) * 2;  // 1:2 divider
    if (mv <= 3000) return 0;
    if (mv >= 4200) return 100;
    return (int)((mv - 3000) * 100 / 1200);
}

}  // namespace platform::battery
