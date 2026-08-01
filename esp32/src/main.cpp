// ─────────────────────────────────────────────────────────────────────────────
// BLE-only local-logging firmware for the Seeed XIAO ESP32-S3 + Polar H10.
//
// Two modes, chosen automatically:
//
//   RECORD (default, on battery)
//     WiFi is OFF. The board scans for the Polar strap, connects, and the moment
//     it connects a session begins: HR + ACC (25 Hz) are written to onboard
//     flash (LittleFS) as CSV. A brief strap dropout resumes the same session;
//     a long gap finalizes it and the next connect starts a new one.
//       BLE light (GPIO1): blink = scanning, solid ~2 s on connect then off.
//       User light (GPIO21): off on battery (fast blink = flash full).
//
//   SYNC (when plugged into USB)
//     Recording pauses, WiFi comes up, and the stored session files are pushed to
//     the Pi over MQTT (TLS to HiveMQ), then deleted on success.
//       User light (GPIO21): solid on while plugged in; BLE light off.
//
// This file is only the orchestrator — all logic lives in the modules below:
//   ble/       scan, secure connect, HR + ACC decode
//   record/    session files + queue flushing + lifecycle
//   sync/      WiFi join, MQTT uploader, record↔sync mode switching
//   platform/  status LED, battery, USB-host detection
// ─────────────────────────────────────────────────────────────────────────────
#include <Arduino.h>
#include "ble/polar.h"
#include "record/recorder.h"
#include "sync/sync_mode.h"
#include "platform/led.h"
#include "platform/usb_host.h"

void setup() {
    platform::led::begin();   // board power-on + status LED
    Serial.begin(115200);
    record::begin();          // queues + LittleFS (BLE callbacks feed the queues)
    syncmode::begin();            // WiFi off until a USB plug-in
    ble::begin();             // NimBLE scan for the Polar strap
}

void loop() {
    // Mode switching driven by USB presence.
    bool usb = platform::usb::hostPresent();
    if (usb && syncmode::current() == Mode::Record)      syncmode::enter();
    else if (!usb && syncmode::current() == Mode::Sync)  syncmode::exit();

    if (syncmode::current() == Mode::Sync) {
        syncmode::service();
        return;
    }

    // ── Record mode ──────────────────────────────────────────────────────────
    ble::pollConnect();
    ble::serviceScan();
    record::serviceLifecycle(ble::isConnected());
    record::service();
    platform::led::update();

    delay(10);
}
