#pragma once
#include <stdint.h>
#include "config.h"

// Polar BLE protocol constants: the standard Heart Rate service plus Polar's
// Measurement Data (PMD) service that carries the accelerometer stream.
namespace ble {

inline constexpr const char* HR_SVC_UUID  = "0000180D-0000-1000-8000-00805f9b34fb";
inline constexpr const char* HR_CHAR_UUID = "00002A37-0000-1000-8000-00805f9b34fb";

inline constexpr const char* PMD_SVC_UUID  = "FB005C80-02E7-F387-1CAD-8ACD2D8DF0C8";
inline constexpr const char* PMD_CTRL_UUID = "FB005C81-02E7-F387-1CAD-8ACD2D8DF0C8"; // write + indicate
inline constexpr const char* PMD_DATA_UUID = "FB005C82-02E7-F387-1CAD-8ACD2D8DF0C8"; // notify

// Start-measurement command written to the PMD control point.
// [0x02 start][0x02 ACC] then TLV settings: SAMPLE_RATE, RESOLUTION, RANGE.
// Sample-rate word (bytes 4-5) is little-endian: 0x19=25, 0x32=50, 0x64=100, 0xC8=200 Hz.
inline constexpr uint8_t PMD_START_ACC[] = {
    0x02, 0x02,
    0x00, 0x01, ACC_SAMPLE_RATE, 0x00,   // SAMPLE_RATE = ACC_SAMPLE_RATE Hz
    0x01, 0x01, 0x10, 0x00,              // RESOLUTION  = 16 bit
    0x02, 0x01, ACC_RANGE_G, 0x00        // RANGE       = ±ACC_RANGE_G g
};

// Stop-measurement command: [0x03 stop][0x02 ACC]. Halts the Polar's ACC stream.
inline constexpr uint8_t PMD_STOP_ACC[] = { 0x03, 0x02 };

}  // namespace ble
