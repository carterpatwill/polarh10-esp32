#pragma once
#include <stdint.h>

// Shared data structs passed between the BLE producer and the recorder consumer,
// plus the top-level operating mode. No logic lives here.

struct HRReading {
    uint32_t t_ms;
    uint8_t  bpm;
    float    rr_ms[8];
    uint8_t  rr_count;
};

struct ACCSample {
    uint32_t t_ms;    // ESP32 receipt time of the frame this sample arrived in
    int16_t  x, y, z; // milli-g
};

// RECORD = on battery, WiFi off, logging to flash. SYNC = on USB, WiFi up,
// pushing stored recordings to the Pi. Chosen automatically from USB presence.
enum class Mode { Record, Sync };
