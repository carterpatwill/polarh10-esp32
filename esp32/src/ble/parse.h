#pragma once
#include <stddef.h>
#include <stdint.h>

class NimBLERemoteCharacteristic;

// Pure decode of the Polar notification payloads into HRReading/ACCSample, which
// they hand straight to the recorder's queues. Registered as NimBLE notify
// callbacks by the connect path in polar.cpp.
namespace ble {

void onHRNotify (NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool);
void onAccNotify(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool);

}  // namespace ble
