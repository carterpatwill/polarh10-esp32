#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <DNSServer.h>
#include <esp_eap_client.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <PubSubClient.h>
#include <NimBLEDevice.h>
#include <ArduinoJson.h>
#include "config.h"

static const char* HR_SVC_UUID  = "0000180D-0000-1000-8000-00805f9b34fb";
static const char* HR_CHAR_UUID = "00002A37-0000-1000-8000-00805f9b34fb";

// Polar Measurement Data (PMD) service — carries the accelerometer stream
static const char* PMD_SVC_UUID  = "FB005C80-02E7-F387-1CAD-8ACD2D8DF0C8";
static const char* PMD_CTRL_UUID = "FB005C81-02E7-F387-1CAD-8ACD2D8DF0C8"; // write + indicate
static const char* PMD_DATA_UUID = "FB005C82-02E7-F387-1CAD-8ACD2D8DF0C8"; // notify

// Start-measurement command written to the PMD control point.
// [0x02 start][0x02 ACC] then TLV settings: SAMPLE_RATE, RESOLUTION, RANGE.
// Sample-rate word (bytes 4-5) is little-endian: 0x19=25, 0x32=50, 0x64=100, 0xC8=200 Hz.
static const uint8_t PMD_START_ACC[] = {
    0x02, 0x02,
    0x00, 0x01, ACC_SAMPLE_RATE, 0x00,   // SAMPLE_RATE = ACC_SAMPLE_RATE Hz
    0x01, 0x01, 0x10, 0x00,              // RESOLUTION  = 16 bit
    0x02, 0x01, ACC_RANGE_G, 0x00        // RANGE       = ±ACC_RANGE_G g
};

struct HRReading {
    uint32_t t_ms;
    uint8_t  bpm;
    float    rr_ms[8];
    uint8_t  rr_count;
};

struct ACCSample {
    uint32_t t_ms;   // ESP32 receipt time of the frame this sample arrived in
    int16_t  x, y, z; // milli-g
};

static WiFiClientSecure secureClient;
static PubSubClient      mqtt(secureClient);
static QueueHandle_t     hrQueue;
static QueueHandle_t     accQueue;
static volatile bool  doConnect = false;
static NimBLEAddress  polarAddr;
static NimBLEClient*  pClient   = nullptr;
static bool           connected = false;

// Live state exposed to the web server
static volatile uint8_t  lastBPM         = 0;
static volatile uint32_t lastBPMTime_ms  = 0;
static bool              receiverOk      = false;
static uint32_t          lastPostTime_ms = 0;

// Live accelerometer state
static volatile bool     accStreaming    = false;
static volatile int16_t  lastAccX        = 0;
static volatile int16_t  lastAccY        = 0;
static volatile int16_t  lastAccZ        = 0;
static volatile uint32_t lastAccTime_ms  = 0;

static WebServer  webServer(80);
static DNSServer  dnsServer;

// ── Battery ───────────────────────────────────────────────────────────────────
static int readBatteryPercent() {
    uint32_t mv = analogReadMilliVolts(PIN_BAT_ADC) * 2;  // 1:2 divider
    if (mv <= 3000) return 0;
    if (mv >= 4200) return 100;
    return (int)((mv - 3000) * 100 / 1200);
}

// ── Status page HTML ──────────────────────────────────────────────────────────
static const char INDEX_HTML[] = R"html(<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32 Polar</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:monospace;background:#0d1117;color:#c9d1d9;padding:20px}
  h1{color:#58a6ff;margin-bottom:20px;font-size:1.4em}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin-bottom:12px}
  .label{font-size:.7em;color:#8b949e;text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px}
  .value{font-size:1.1em}
  .on{color:#3fb950}.off{color:#f85149}.dim{color:#8b949e}
  #bpm{font-size:3.5em;font-weight:bold;color:#58a6ff;line-height:1}
  #bpm-unit{font-size:.9em;color:#8b949e;margin-top:4px}
  .bat-bar{height:8px;background:#30363d;border-radius:4px;margin-top:8px;overflow:hidden}
  .bat-fill{height:100%;border-radius:4px;transition:width .5s}
</style>
</head>
<body>
<h1>ESP32 Polar H10</h1>
<div class="dim" id="addr" style="font-size:.8em;margin-bottom:16px"></div>
<div class="card">
  <div class="label">Battery</div>
  <div class="value" id="bat">—</div>
  <div class="bat-bar"><div class="bat-fill" id="bat-fill" style="width:0%"></div></div>
</div>
<div class="card">
  <div class="label">Bluetooth</div>
  <div class="value" id="ble">—</div>
</div>
<div class="card" id="hr-card" style="display:none">
  <div class="label">Heart Rate</div>
  <div id="bpm">—</div>
  <div id="bpm-unit"></div>
</div>
<div class="card" id="acc-card" style="display:none">
  <div class="label">Accelerometer (mg)</div>
  <div class="value"><span class="dim">X</span> <span id="ax">—</span>
       &nbsp; <span class="dim">Y</span> <span id="ay">—</span>
       &nbsp; <span class="dim">Z</span> <span id="az">—</span></div>
</div>
<div class="card">
  <div class="label">MQTT Broker</div>
  <div class="value" id="recv">—</div>
</div>
<script>
async function tick(){
  try{
    const d=await(await fetch('/status')).json();

    // Address (mDNS + LAN IP)
    document.getElementById('addr').textContent=
      'http://'+d.mdns_host+'/  •  '+d.lan_ip;

    // Battery
    const batEl=document.getElementById('bat');
    const fill=document.getElementById('bat-fill');
    const pct=d.battery_pct;
    batEl.textContent=pct+'%';
    fill.style.width=pct+'%';
    fill.style.background=pct>50?'#3fb950':pct>20?'#d29922':'#f85149';

    // BLE
    const ble=document.getElementById('ble');
    const hr=document.getElementById('hr-card');
    const bpm=document.getElementById('bpm');
    const unit=document.getElementById('bpm-unit');
    if(d.ble_connected){
      ble.textContent='Connected ✓';ble.className='value on';
      hr.style.display='block';
      bpm.textContent=d.bpm;
      const age=Math.round(d.bpm_age_ms/1000);
      unit.textContent=age<5?'BPM • live':'BPM • '+age+'s ago';
    } else {
      ble.textContent='Scanning for Polar…';ble.className='value off';
      hr.style.display='none';
    }

    // Accelerometer
    const accCard=document.getElementById('acc-card');
    if(d.acc_streaming){
      accCard.style.display='block';
      document.getElementById('ax').textContent=d.acc_x;
      document.getElementById('ay').textContent=d.acc_y;
      document.getElementById('az').textContent=d.acc_z;
    } else {
      accCard.style.display='none';
    }

    // HR receiver
    const recv=document.getElementById('recv');
    if(d.receiver_ok){
      const sec=Math.round(d.last_post_ms/1000);
      recv.textContent='Connected to MQTT ✓  (last publish '+sec+'s ago)';recv.className='value on';
    } else {
      recv.textContent='Not connected to MQTT';recv.className='value off';
    }
  }catch(e){}
}
tick();setInterval(tick,1000);
</script>
</body>
</html>)html";

// ── Web server handlers ───────────────────────────────────────────────────────
static void handleRoot() {
    webServer.send(200, "text/html", INDEX_HTML);
}

static void handleStatus() {
    JsonDocument doc;
    doc["ble_connected"] = connected;
    doc["bpm"]           = (int)lastBPM;
    doc["bpm_age_ms"]    = connected ? (int32_t)(millis() - lastBPMTime_ms) : -1;
    doc["receiver_ok"]   = receiverOk;
    doc["last_post_ms"]  = receiverOk ? (int32_t)(millis() - lastPostTime_ms) : -1;
    doc["server_url"]    = MQTT_HOST;
    doc["lan_ip"]        = WiFi.localIP().toString();
    doc["mdns_host"]     = String(MDNS_HOST) + ".local";
    doc["battery_pct"]   = readBatteryPercent();
    doc["acc_streaming"] = accStreaming;
    doc["acc_x"]         = (int)lastAccX;
    doc["acc_y"]         = (int)lastAccY;
    doc["acc_z"]         = (int)lastAccZ;
    doc["acc_age_ms"]    = accStreaming ? (int32_t)(millis() - lastAccTime_ms) : -1;
    String out;
    serializeJson(doc, out);
    webServer.send(200, "application/json", out);
}

// Captive portal: redirect every unknown path to the status page
static void handleCaptive() {
    webServer.sendHeader("Location", "http://192.168.4.1/", true);
    webServer.send(302, "text/plain", "");
}

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

    lastBPM        = r.bpm;
    lastBPMTime_ms = r.t_ms;

    xQueueSend(hrQueue, &r, 0);
    Serial.printf("[HR] %d BPM\n", r.bpm);
}

// ── PMD helpers: read `bits` bits (LSB-first) at a bit offset, sign-extended ──
static int32_t readSignedBits(const uint8_t* data, size_t bitPos, uint8_t bits) {
    int32_t value = 0;
    for (uint8_t i = 0; i < bits; i++) {
        size_t  bytePos = (bitPos + i) / 8;
        uint8_t bit     = (data[bytePos] >> ((bitPos + i) % 8)) & 0x01;
        value |= (int32_t)bit << i;
    }
    if (bits < 32 && (value & (1 << (bits - 1)))) value |= (~0 << bits); // sign extend
    return value;
}

static inline void emitAccSample(int32_t x, int32_t y, int32_t z, uint32_t t_ms) {
    ACCSample s{ t_ms, (int16_t)x, (int16_t)y, (int16_t)z };
    lastAccX = s.x; lastAccY = s.y; lastAccZ = s.z; lastAccTime_ms = t_ms;
    xQueueSend(accQueue, &s, 0);
}

// ── ACC notification callback (PMD data char, delta-compressed frames) ───────
static void onAccNotify(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
    // [0] measurement type (0x02 = ACC)  [1..8] timestamp (u64 ns)  [9] frame type
    if (len < 16 || data[0] != 0x02) return;
    uint32_t t_ms = millis();
    uint8_t  frameType = data[9];

    if (frameType == 0x01) {
        // Uncompressed: consecutive int16 (x,y,z) triples in milli-g, 6 bytes each.
        for (size_t off = 10; off + 6 <= len; off += 6) {
            int16_t x = (int16_t)(data[off]     | (uint16_t(data[off + 1]) << 8));
            int16_t y = (int16_t)(data[off + 2] | (uint16_t(data[off + 3]) << 8));
            int16_t z = (int16_t)(data[off + 4] | (uint16_t(data[off + 5]) << 8));
            emitAccSample(x, y, z, t_ms);
        }
        return;
    }

    // Fallback: delta/compressed frame (not produced by the H10 at this config).
    // Reference sample (int16 ×3) followed by byte-aligned [deltaSize][count] groups.
    int32_t x = (int16_t)(data[10] | (uint16_t(data[11]) << 8));
    int32_t y = (int16_t)(data[12] | (uint16_t(data[13]) << 8));
    int32_t z = (int16_t)(data[14] | (uint16_t(data[15]) << 8));
    emitAccSample(x, y, z, t_ms);
    size_t offset = 16;
    while (offset + 2 <= len) {
        uint8_t deltaSize   = data[offset++];
        uint8_t sampleCount = data[offset++];
        if (deltaSize == 0) break;
        size_t bitPos = offset * 8;
        for (uint8_t s = 0; s < sampleCount; s++) {
            x += readSignedBits(data, bitPos, deltaSize); bitPos += deltaSize;
            y += readSignedBits(data, bitPos, deltaSize); bitPos += deltaSize;
            z += readSignedBits(data, bitPos, deltaSize); bitPos += deltaSize;
            emitAccSample(x, y, z, t_ms);
        }
        offset += ((size_t)sampleCount * 3 * deltaSize + 7) / 8;
    }
}

// ── PMD control-point indication: log the device's response ───────────────────
static void onPmdControl(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
    if (len >= 4 && data[0] == 0xF0 && data[1] == 0x02) {  // response to a start-measurement cmd
        uint8_t status = data[3];
        Serial.printf("[ACC] PMD start response: status=%d %s\n",
                      status, status == 0 ? "(OK)" : "(error)");
        accStreaming = (status == 0);
    }
}

// ── Backlight / status LED ────────────────────────────────────────────────────
static constexpr int PIN_BL = 38;

static void flashOnBL(int times = 3) {
    for (int i = 0; i < times; i++) {
        digitalWrite(PIN_BL, LOW);  delay(80);
        digitalWrite(PIN_BL, HIGH); delay(80);
    }
    digitalWrite(PIN_BL, HIGH);
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
        connected    = false;
        accStreaming = false;
        pClient      = nullptr;
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

    // ── Start the PMD accelerometer stream (best-effort; HR still works if absent)
    accStreaming = false;
    auto* pmd = pClient->getService(PMD_SVC_UUID);
    if (pmd) {
        auto* dataChr = pmd->getCharacteristic(PMD_DATA_UUID);
        auto* ctrlChr = pmd->getCharacteristic(PMD_CTRL_UUID);
        if (dataChr && ctrlChr && dataChr->canNotify()) {
            dataChr->subscribe(true, onAccNotify);         // notifications for the sample stream
            ctrlChr->subscribe(false, onPmdControl);       // indications for the command response
            if (ctrlChr->writeValue(PMD_START_ACC, sizeof(PMD_START_ACC), true)) {
                Serial.printf("[ACC] Requested ACC stream @ %d Hz, ±%d g\n",
                              ACC_SAMPLE_RATE, ACC_RANGE_G);
            } else {
                Serial.println("[ACC] Failed to write PMD start command");
            }
        } else {
            Serial.println("[ACC] PMD characteristics not found");
        }
    } else {
        Serial.println("[ACC] PMD service not found (device may not support ACC)");
    }

    digitalWrite(PIN_BL, LOW);
    return true;
}

// ── (Re)connect to HiveMQ Cloud over TLS ──────────────────────────────────────
static bool mqttConnect() {
    if (mqtt.connected()) return true;
    if (WiFi.status() != WL_CONNECTED) return false;

    Serial.print("[MQTT] Connecting to "); Serial.print(MQTT_HOST); Serial.print("...");
    if (mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS)) {
        Serial.println(" connected");
        receiverOk = true;
        return true;
    }
    Serial.printf(" failed, rc=%d\n", mqtt.state());
    receiverOk = false;
    return false;
}

// ── Drain queue and publish JSON batch to HiveMQ ──────────────────────────────
static void sendBatch() {
    if (!mqttConnect()) return;

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

    if (mqtt.publish(MQTT_TOPIC, body.c_str())) {
        receiverOk      = true;
        lastPostTime_ms = millis();
        Serial.printf("[MQTT] Published %d readings (%d bytes) → %s\n", count, body.length(), MQTT_TOPIC);
    } else {
        receiverOk = false;
        Serial.printf("[MQTT] Publish failed (buffer too small? state=%d)\n", mqtt.state());
    }
}

// ── Drain ACC queue and publish JSON batch (all 3 axes) to HiveMQ ─────────────
static void sendAccBatch() {
    if (uxQueueMessagesWaiting(accQueue) == 0) return;
    if (!mqttConnect()) return;

    ACCSample s;
    JsonDocument doc;
    doc["sample_rate_hz"] = ACC_SAMPLE_RATE;
    doc["range_g"]        = ACC_RANGE_G;
    JsonArray arr = doc["samples"].to<JsonArray>();
    int count = 0;

    while (xQueueReceive(accQueue, &s, 0) == pdTRUE) {
        JsonArray xyz = arr.add<JsonArray>();   // compact [t_ms, x, y, z]
        xyz.add(s.t_ms);
        xyz.add(s.x);
        xyz.add(s.y);
        xyz.add(s.z);
        count++;
    }

    if (count == 0) return;

    String body;
    serializeJson(doc, body);

    if (mqtt.publish(MQTT_TOPIC_ACC, body.c_str())) {
        lastPostTime_ms = millis();
        Serial.printf("[MQTT] Published %d ACC samples (%d bytes) → %s\n", count, body.length(), MQTT_TOPIC_ACC);
    } else {
        Serial.printf("[MQTT] ACC publish failed (buffer too small? state=%d)\n", mqtt.state());
    }
}

// ── Start AP + captive portal DNS ────────────────────────────────────────────
static void startAP() {
    WiFi.softAP(AP_SSID);
    IPAddress apIP = WiFi.softAPIP();
    // DNS: answer every hostname with the AP IP so captive portal triggers
    dnsServer.start(53, "*", apIP);
    Serial.printf("[AP] %s  →  http://%s\n", AP_SSID, apIP.toString().c_str());
}

// ─────────────────────────────────────────────────────────────────────────────
void setup() {
    pinMode(15, OUTPUT);
    digitalWrite(15, HIGH);

    pinMode(PIN_BL, OUTPUT);
    digitalWrite(PIN_BL, HIGH);

    Serial.begin(115200);
    delay(1000);

    hrQueue  = xQueueCreate(QUEUE_LEN, sizeof(HRReading));
    accQueue = xQueueCreate(QUEUE_LEN_ACC, sizeof(ACCSample));

    // Start AP + captive portal immediately
    WiFi.mode(WIFI_AP_STA);
    startAP();

    webServer.on("/",       handleRoot);
    webServer.on("/status", handleStatus);
    webServer.onNotFound(handleCaptive);   // any other path → redirect to portal
    webServer.begin();
    Serial.println("[Web] Status server up on AP");

    // Try personal network first (10 s)
    Serial.print("[WiFi] Trying personal network");
    WiFi.begin(HOME_SSID, HOME_PASS);
    {
        bool bl = true;
        for (int i = 0; i < 20 && WiFi.status() != WL_CONNECTED; i++) {
            bl = !bl;
            digitalWrite(PIN_BL, bl ? HIGH : LOW);
            delay(500); Serial.print(".");
        }
    }

    if (WiFi.status() != WL_CONNECTED) {
        Serial.print("\n[WiFi] Trying eduroam");
        WiFi.disconnect(true);
        delay(500);
        // Restore AP+STA after disconnect(true) tears down WiFi
        WiFi.mode(WIFI_AP_STA);
        startAP();
        esp_eap_client_set_identity((uint8_t*)ENT_IDENTITY, strlen(ENT_IDENTITY));
        esp_eap_client_set_username((uint8_t*)ENT_USER,     strlen(ENT_USER));
        esp_eap_client_set_password((uint8_t*)ENT_PASS,     strlen(ENT_PASS));
        esp_wifi_sta_enterprise_enable();
        WiFi.begin(ENT_SSID);
        bool bl = true;
        while (WiFi.status() != WL_CONNECTED) {
            bl = !bl;
            digitalWrite(PIN_BL, bl ? HIGH : LOW);
            delay(500); Serial.print(".");
        }
    }

    Serial.printf("\n[WiFi] IP: %s\n", WiFi.localIP().toString().c_str());

    // mDNS: reachable at http://<MDNS_HOST>.local/ on the same WiFi
    if (MDNS.begin(MDNS_HOST)) {
        MDNS.addService("http", "tcp", 80);
        Serial.printf("[mDNS] Status page: http://%s.local/  (or http://%s/)\n",
                      MDNS_HOST, WiFi.localIP().toString().c_str());
    } else {
        Serial.println("[mDNS] start failed");
    }

    flashOnBL();

    // MQTT over TLS to HiveMQ Cloud.
    // setInsecure() skips server-certificate validation — simplest to get running.
    // For real cert pinning, replace with secureClient.setCACert(<HiveMQ root CA>).
    secureClient.setInsecure();
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setBufferSize(8192);   // ACC batches are large; well above PubSubClient's 256-byte default
    mqttConnect();

    NimBLEDevice::init("ESP32-Polar");
    auto* scan = NimBLEDevice::getScan();
    scan->setScanCallbacks(new ScanCB(), false);
    scan->setActiveScan(true);
    scan->setInterval(100);
    scan->setWindow(99);
    scan->start(0);
    Serial.println("[BLE] Scanning for Polar H10...");
}

static uint32_t lastSend    = 0;
static uint32_t lastAccSend = 0;
static uint32_t lastBLTick  = 0;
static bool     blState     = false;

void loop() {
    dnsServer.processNextRequest();
    webServer.handleClient();

    if (mqtt.connected()) mqtt.loop();
    else                  receiverOk = false;

    if (doConnect) {
        doConnect = false;
        connectToPolar();
    }

    if (!connected && pClient == nullptr) {
        if (!NimBLEDevice::getScan()->isScanning()) {
            NimBLEDevice::getScan()->start(0, false);
            Serial.println("[BLE] Restarted scan");
        }
        uint32_t now = millis();
        if (now - lastBLTick >= 600) {
            lastBLTick = now;
            blState = !blState;
            digitalWrite(PIN_BL, blState ? HIGH : LOW);
        }
    }

    if (millis() - lastSend >= BATCH_MS) {
        lastSend = millis();
        if (WiFi.status() == WL_CONNECTED) sendBatch();
    }

    if (millis() - lastAccSend >= ACC_BATCH_MS) {
        lastAccSend = millis();
        if (WiFi.status() == WL_CONNECTED) sendAccBatch();
    }

    delay(10);
}
