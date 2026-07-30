#pragma once
#include "types.h"

// Owns the session-recording pipeline: two FreeRTOS queues that BLE callbacks
// feed, and the LittleFS session files those queues drain into. A session opens
// on the first strap connect and survives brief dropouts; a long gap finalizes it.
namespace record {

void begin();                             // create queues + mount LittleFS
void enqueueHr (const HRReading& r);      // BLE producer → HR queue (no-op if idle)
void enqueueAcc(const ACCSample& s);      // BLE producer → ACC queue (no-op if idle)

void openIfNeeded();                      // on (re)connect: open a session, arm resume
void notifyDisconnected();                // strap dropped: start the resume timer
void service();                           // flush queues on interval + flash-full guard
void serviceLifecycle(bool connected);    // finalize a session once the resume window lapses
void close();                             // finalize the open session now (sync entry)

bool isActive();
bool isFlashFull();

}  // namespace record
