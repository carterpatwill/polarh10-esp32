#include "ble/polar.h"
#include "ble/parse.h"
#include "ble/pmd.h"
#include "record/recorder.h"
#include <Arduino.h>
#include <NimBLEDevice.h>

namespace ble {

static volatile bool  doConnect    = false;
static NimBLEAddress  polarAddr;
static NimBLEClient*  pClient      = nullptr;
static volatile bool  connected    = false;
static volatile bool  accStreaming = false;

// PMD control-point handle, kept live so a resumed session can restart the ACC
// stream without reconnecting.
static NimBLERemoteCharacteristic* pmdCtrlChr = nullptr;

// ── PMD control-point indication: log the device's response ───────────────────
static void onPmdControl(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
    if (len >= 4 && data[0] == 0xF0 && data[1] == 0x02) {  // response to a start-measurement cmd
        uint8_t status = data[3];
        Serial.printf("[ACC] PMD start response: status=%d %s\n",
                      status, status == 0 ? "(OK)" : "(error)");
        accStreaming = (status == 0);
    }
}

// ── BLE scan: match any Polar device ─────────────────────────────────────────
class ScanCB : public NimBLEScanCallbacks {
    void onResult(const NimBLEAdvertisedDevice* dev) override {
        if (dev->getName().find("Polar") != std::string::npos) {
            Serial.printf("[BLE] Found: %s\n", dev->getName().c_str());
            NimBLEDevice::getScan()->stop();
            polarAddr = dev->getAddress();
            doConnect = true;
        }
    }
};

// ── BLE client: mark disconnect time so the loop can resume-or-finalize ───────
class ClientCB : public NimBLEClientCallbacks {
    void onDisconnect(NimBLEClient*, int reason) override {
        connected      = false;
        accStreaming   = false;
        pClient        = nullptr;
        pmdCtrlChr     = nullptr;
        record::notifyDisconnected();   // recorder times the resume window
        Serial.printf("[BLE] Disconnected (%d), will rescan\n", reason);
    }
};

static ClientCB clientCb;   // one shared callbacks instance (not per-connect)

// ── Connect to Polar, subscribe to HR + ACC, open/resume the session ─────────
// Uses a LOCAL client handle throughout: the strap can drop mid-setup and the
// onDisconnect callback (NimBLE task) nulls the global pClient, so re-reading the
// global here would dereference null. We hold our own pointer and bail out via
// isConnected() checks instead.
static bool connectToPolar() {
    // Reuse a previously-created (now disconnected) client so repeated reconnects
    // don't exhaust NimBLE's small client pool.
    NimBLEClient* cl = NimBLEDevice::getDisconnectedClient();
    if (!cl) cl = NimBLEDevice::createClient();
    if (!cl) { Serial.println("[BLE] No client available"); return false; }
    cl->setClientCallbacks(&clientCb, false);
    pClient = cl;

    if (!cl->connect(polarAddr)) {
        Serial.println("[BLE] Connect failed");
        if (pClient == cl) pClient = nullptr;   // keep the client object for reuse
        return false;
    }

    // The Polar H10 only emits the PMD accelerometer stream over an encrypted link
    // (HR works in the clear, ACC does not). Bond/encrypt before touching PMD.
    cl->secureConnection();
    if (!cl->isConnected()) { Serial.println("[BLE] Dropped during secure setup"); return false; }

    auto* svc = cl->getService(HR_SVC_UUID);
    if (!svc) {
        Serial.println("[BLE] HR service not found");
        cl->disconnect();
        return false;
    }

    auto* chr = svc->getCharacteristic(HR_CHAR_UUID);
    if (!chr || !chr->canNotify()) {
        Serial.println("[BLE] HR char not found or not notifiable");
        cl->disconnect();
        return false;
    }

    chr->subscribe(true, onHRNotify);
    connected = true;
    Serial.println("[BLE] Subscribed to HR notifications");

    // A session begins on connect. If we dropped only briefly (within the resume
    // window) the existing session is still open, so keep writing to it; otherwise
    // open a fresh one. openIfNeeded() also cancels the pending resume timer.
    record::openIfNeeded();

    // ── Start the PMD accelerometer stream (best-effort; HR still works if absent)
    accStreaming = false;
    if (!cl->isConnected()) { Serial.println("[BLE] Dropped before PMD setup"); return true; }
    auto* pmd = cl->getService(PMD_SVC_UUID);
    if (pmd) {
        auto* dataChr = pmd->getCharacteristic(PMD_DATA_UUID);
        auto* ctrlChr = pmd->getCharacteristic(PMD_CTRL_UUID);
        if (dataChr && ctrlChr && dataChr->canNotify()) {
            dataChr->subscribe(true, onAccNotify);         // notifications for the sample stream
            ctrlChr->subscribe(false, onPmdControl);       // indications for the command response
            pmdCtrlChr = ctrlChr;
            if (record::isActive()) {
                // Clear any stream still running from a prior connection first. The
                // H10 keeps ACC going after the ESP drops, so a bare START then
                // returns "already in state" (status 6) and no data flows. Stop,
                // let it settle, then start clean → status 0.
                ctrlChr->writeValue(PMD_STOP_ACC, sizeof(PMD_STOP_ACC), true);
                delay(150);
                if (ctrlChr->writeValue(PMD_START_ACC, sizeof(PMD_START_ACC), true)) {
                    Serial.printf("[ACC] Requested ACC stream @ %d Hz, ±%d g\n",
                                  ACC_SAMPLE_RATE, ACC_RANGE_G);
                } else {
                    Serial.println("[ACC] Failed to write PMD start command");
                }
            }
        } else {
            Serial.println("[ACC] PMD characteristics not found");
        }
    } else {
        Serial.println("[ACC] PMD service not found (device may not support ACC)");
    }

    return true;
}

// ── Public interface ─────────────────────────────────────────────────────────
void begin() {
    NimBLEDevice::init("ESP32-Polar");
    // Polar PMD only streams over an encrypted link. Bond, Just-Works (no MITM),
    // LE Secure Connections — so the H10 will emit the ACC data.
    NimBLEDevice::setSecurityAuth(true, false, true);
    auto* scan = NimBLEDevice::getScan();
    scan->setScanCallbacks(new ScanCB(), false);
    scan->setActiveScan(true);
    scan->setInterval(100);
    scan->setWindow(99);
    scan->start(0);
    Serial.println("[BLE] Scanning for Polar H10...");
}

void pollConnect() {
    if (doConnect) {
        doConnect = false;
        connectToPolar();
    }
}

void serviceScan() {
    // No strap: keep scanning so we can find it again.
    if (!connected && pClient == nullptr && !NimBLEDevice::getScan()->isScanning()) {
        NimBLEDevice::getScan()->start(0, false);
        Serial.println("[BLE] Restarted scan");
    }
}

void startScan() {
    NimBLEDevice::getScan()->start(0, false);
}

void stopAndDisconnect() {
    NimBLEDevice::getScan()->stop();
    if (pClient) { pClient->disconnect(); pClient = nullptr; }
    connected = false;
}

bool isConnected() { return connected; }

}  // namespace ble
