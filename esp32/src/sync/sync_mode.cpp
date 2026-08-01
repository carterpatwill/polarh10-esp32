#include "sync/sync_mode.h"
#include "sync/wifi.h"
#include "sync/uploader.h"
#include "ble/polar.h"
#include "record/recorder.h"
#include "platform/led.h"
#include <Arduino.h>
#include <WiFi.h>
#include <time.h>   // NTP

namespace syncmode {

static Mode mode         = Mode::Record;
static bool syncUploaded = false;   // upload pass already run this sync session

void begin() {
    WiFi.mode(WIFI_OFF);   // WiFi stays off until sync mode
    mode = Mode::Record;
}

void enter() {
    Serial.println("\n[SYNC] USB detected — entering sync mode");
    // Stop recording: close any open session and drop the strap so BLE is idle.
    record::close();
    ble::stopAndDisconnect();
    platform::led::bleSet(false);   // BLE LED off for the whole sync session

    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    bool joined = joinAny();

    if (joined) {
        Serial.printf("\n[SYNC] WiFi up, IP %s — pushing recordings to the Pi\n",
                      WiFi.localIP().toString().c_str());
        // Grab NTP time so each session's real start can be reconstructed from millis().
        // UTC epoch (offset 0); the Pi renders it in local time. Wait briefly for a fix.
        configTime(0, 0, "pool.ntp.org", "time.nist.gov");
        struct tm tmNow;
        for (int i = 0; i < 20 && !getLocalTime(&tmNow, 200); i++) { /* up to ~4s */ }
        Serial.printf("[SYNC] NTP time: %ld\n", (long)time(nullptr));
        uploader::beginLink();
    } else {
        Serial.println("\n[SYNC] WiFi failed — check ENT_*/SYNC_SSID/PASS in config.h");
    }

    syncUploaded = false;   // the loop runs one upload pass once the link is up
    mode = Mode::Sync;
}

void exit() {
    Serial.println("[SYNC] USB removed — resuming record mode");
    uploader::disconnect();
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    mode = Mode::Record;
    // Restart the scan so we reconnect to the strap and open a new session.
    ble::startScan();
}

void service() {
    // Once WiFi + MQTT are up, run a single pass that pushes every stored
    // recording to the Pi, then idle (solid LED) until USB is removed.
    if (!syncUploaded && WiFi.status() == WL_CONNECTED && uploader::connect()) {
        uploader::runUploads();
        syncUploaded = true;
    }
    uploader::loop();
    platform::led::update();
    delay(5);
}

Mode current()    { return mode; }
bool uploadDone() { return syncUploaded; }

}  // namespace syncmode
