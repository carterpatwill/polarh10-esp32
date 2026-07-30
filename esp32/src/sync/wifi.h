#pragma once
#include <stdint.h>

// WiFi association for sync mode. Tries eduroam (WPA2-Enterprise) first, then the
// personal WPA2 networks as fallbacks. Blocks (fast-blinking the LED) until a link
// comes up or the attempts time out.
namespace syncmode {

bool joinAny();

}  // namespace syncmode
