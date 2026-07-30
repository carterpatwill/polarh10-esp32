#pragma once

// The BLE side of record mode: scans for the Polar strap, brings up an encrypted
// link, subscribes to HR + ACC, and drives reconnects. Owns the NimBLE client and
// connection state; feeds decoded samples into the recorder via parse.cpp.
namespace ble {

void begin();              // NimBLE init + security + start scanning
void pollConnect();        // act on a pending scan hit → connect
void serviceScan();        // restart the scan when the strap is absent
void startScan();          // (re)start scanning explicitly
void stopAndDisconnect();  // drop the strap and stop scanning (sync-mode entry)
bool isConnected();

}  // namespace ble
