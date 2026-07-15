#include <Arduino.h>
#include <WiFi.h>
#include <esp_eap_client.h>
#include <HTTPClient.h>
#include <NimBLEDevice.h>
#include <ArduinoJson.h>
#include "config.h"

static const char* HR_SVC_UUID  = "0000180D-0000-1000-8000-00805f9b34fb";
static const char* HR_CHAR_UUID = "00002A37-0000-1000-8000-00805f9b34fb";

struct HRReading {
    uint32_t t_ms;
    uint8_t  bpm;
    float    rr_ms[8];
    uint8_t  rr_count;
};

static QueueHandle_t  hrQueue;
static volatile bool  doConnect = false;
static NimBLEAddress  polarAddr;
static NimBLEClient*  pClient   = nullptr;
static bool           connected = false;

// ── HR notification callback (runs in NimBLE task) ───────────────────────────
static void onHRNotify(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
    if (len < 2) return;

    HRReading r{};
    r.t_ms = millis();

    uint8_t flags  = data[0];
    size_t  offset = 1;

    if (flags & 0x01) {
        if (len < 3) return;
        r.bpm  = data[1] | (uint16_t(data[2]) << 8);
        offset = 3;
    } else {
        r.bpm  = data[1];
        offset = 2;
    }

    if (flags & 0x10) {
        while (offset + 1 < len && r.rr_count < 8) {
            uint16_t raw = data[offset] | (uint16_t(data[offset + 1]) << 8);
            r.rr_ms[r.rr_count++] = raw / 1024.0f * 1000.0f;
            offset += 2;
        }
    }

    xQueueSend(hrQueue, &r, 0);   // non-blocking; drop oldest if full
    Serial.printf("[HR] %d BPM\n", r.bpm);
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

// ── BLE client: auto-rescan on disconnect ─────────────────────────────────────
class ClientCB : public NimBLEClientCallbacks {
    void onDisconnect(NimBLEClient*, int reason) override {
        connected = false;
        pClient   = nullptr;
        Serial.printf("[BLE] Disconnected (%d), will rescan\n", reason);
    }
};

// ── Connect to Polar and subscribe to HR ─────────────────────────────────────
static bool connectToPolar() {
    pClient = NimBLEDevice::createClient();
    pClient->setClientCallbacks(new ClientCB(), false);

    if (!pClient->connect(polarAddr)) {
        Serial.println("[BLE] Connect failed");
        NimBLEDevice::deleteClient(pClient);
        pClient = nullptr;
        return false;
    }

    auto* svc = pClient->getService(HR_SVC_UUID);
    if (!svc) {
        Serial.println("[BLE] HR service not found");
        pClient->disconnect();
        return false;
    }

    auto* chr = svc->getCharacteristic(HR_CHAR_UUID);
    if (!chr || !chr->canNotify()) {
        Serial.println("[BLE] HR char not found or not notifiable");
        pClient->disconnect();
        return false;
    }

    chr->subscribe(true, onHRNotify);
    connected = true;
    Serial.println("[BLE] Subscribed to HR notifications");
    return true;
}

// ── Drain queue and POST JSON batch to server ─────────────────────────────────
static void sendBatch() {
    HRReading r;
    JsonDocument doc;
    JsonArray arr = doc["readings"].to<JsonArray>();
    int count = 0;

    while (xQueueReceive(hrQueue, &r, 0) == pdTRUE) {
        JsonObject obj = arr.add<JsonObject>();
        obj["t_ms"] = r.t_ms;
        obj["bpm"]  = r.bpm;
        if (r.rr_count > 0) {
            JsonArray rr = obj["rr_ms"].to<JsonArray>();
            for (int i = 0; i < r.rr_count; i++) rr.add(r.rr_ms[i]);
        }
        count++;
    }

    if (count == 0) return;

    String body;
    serializeJson(doc, body);

    HTTPClient http;
    http.begin(SERVER_URL);
    http.addHeader("Content-Type", "application/json");
    int code = http.POST(body);
    http.end();

    if (code > 0) {
        Serial.printf("[WiFi] Sent %d readings → HTTP %d\n", count, code);
    } else {
        Serial.printf("[WiFi] POST failed: %s\n", http.errorToString(code).c_str());
    }
}

// ─────────────────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(1000);

    hrQueue = xQueueCreate(QUEUE_LEN, sizeof(HRReading));

    Serial.print("[WiFi] Connecting (WPA2-Enterprise)");
    WiFi.mode(WIFI_STA);
    esp_eap_client_set_identity((uint8_t*)WIFI_IDENTITY, strlen(WIFI_IDENTITY));
    esp_eap_client_set_username((uint8_t*)WIFI_USER,     strlen(WIFI_USER));
    esp_eap_client_set_password((uint8_t*)WIFI_PASS,     strlen(WIFI_PASS));
    esp_wifi_sta_enterprise_enable();
    WiFi.begin(WIFI_SSID);
    while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
    Serial.printf("\n[WiFi] IP: %s\n", WiFi.localIP().toString().c_str());

    NimBLEDevice::init("ESP32-Polar");
    auto* scan = NimBLEDevice::getScan();
    scan->setScanCallbacks(new ScanCB(), false);
    scan->setActiveScan(true);
    scan->setInterval(100);
    scan->setWindow(99);
    scan->start(0);
    Serial.println("[BLE] Scanning for Polar H10...");
}

static uint32_t lastSend = 0;

void loop() {
    if (doConnect) {
        doConnect = false;
        connectToPolar();
    }

    if (!connected && pClient == nullptr) {
        if (!NimBLEDevice::getScan()->isScanning()) {
            NimBLEDevice::getScan()->start(0, false);
            Serial.println("[BLE] Restarted scan");
        }
    }

    if (millis() - lastSend >= BATCH_MS) {
        lastSend = millis();
        if (WiFi.status() == WL_CONNECTED) sendBatch();
    }

    delay(10);
}
