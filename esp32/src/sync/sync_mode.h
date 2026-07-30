#pragma once
#include "types.h"

// Top-level sync/record mode orchestration. Owns the current Mode and the
// once-per-plug-in "upload done" latch, and wires together WiFi, the uploader,
// the recorder and BLE when USB presence flips the mode.
namespace syncmode {

void begin();        // initial state: WiFi off, record mode
void enter();        // USB detected: stop recording, bring WiFi + MQTT up
void exit();         // USB removed: tear WiFi down, resume record mode
void service();      // the loop's sync-mode branch (one upload pass, then idle)

Mode current();
bool uploadDone();

}  // namespace syncmode
