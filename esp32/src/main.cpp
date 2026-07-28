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
//       LED: solid = recording, slow blink = scanning, triple-blink = flash full.
//
//   SYNC (when plugged into USB)
//     Recording pauses, WiFi comes up, and a tiny web server serves the stored
//     session files so you can download them, then delete/wipe. The board prints
//     its URL to the serial monitor (USB is connected, so you'll see it).
//       LED: fast blink.
//
// No MQTT, no control page, no cloud — everything lives on the device until you
// offload it over USB.
// ─────────────────────────────────────────────────────────────────────────────
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <WiFi.h>
#include <WebServer.h>
#include <LittleFS.h>
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

// Stop-measurement command: [0x03 stop][0x02 ACC]. Halts the Polar's ACC stream.
static const uint8_t PMD_STOP_ACC[] = { 0x03, 0x02 };

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

static QueueHandle_t  hrQueue;
static QueueHandle_t  accQueue;
static volatile bool  doConnect = false;
static NimBLEAddress  polarAddr;
static NimBLEClient*  pClient   = nullptr;
static volatile bool  connected = false;
static volatile bool  accStreaming = false;

// PMD control-point handle, kept live so a resumed session can restart the ACC
// stream without reconnecting.
static NimBLERemoteCharacteristic* pmdCtrlChr = nullptr;

// ── Session state ─────────────────────────────────────────────────────────────
// A session opens on the first strap connect and stays open across brief
// dropouts. Data timestamps are milliseconds relative to session start; there is
// no real-world clock on the board (sessions are sequentially numbered instead).
static bool      sessionActive   = false;   // a session file is currently open
static uint16_t  sessionNum      = 0;       // sequential id of the open session
static uint32_t  sessionStart_ms = 0;       // millis() at session open
static uint32_t  disconnectedAt  = 0;       // millis() of last strap drop (0 = connected)
static File      hrFile;
static File      accFile;
static bool      flashFull       = false;   // stopped writing: LittleFS below margin

// ── Operating mode ────────────────────────────────────────────────────────────
enum Mode { MODE_RECORD, MODE_SYNC };
static Mode      mode = MODE_RECORD;
static WebServer server(80);

// ── Battery ───────────────────────────────────────────────────────────────────
static int readBatteryPercent() {
    if (PIN_BAT_ADC < 0) return -1;                       // no battery divider wired
    uint32_t mv = analogReadMilliVolts(PIN_BAT_ADC) * 2;  // 1:2 divider
    if (mv <= 3000) return 0;
    if (mv >= 4200) return 100;
    return (int)((mv - 3000) * 100 / 1200);
}

// ─────────────────────────────────────────────────────────────────────────────
// Session files on LittleFS
//
// Each session is two CSVs:   /s0001_hr.csv   and   /s0001_acc.csv
//   hr:  t_ms,bpm,rr_ms        (rr_ms is ';'-joined, may be empty)
//   acc: t_ms,x,y,z            (milli-g)
// t_ms is milliseconds since the session opened.
// ─────────────────────────────────────────────────────────────────────────────

// Scan the filesystem for the highest existing session number so a new session
// never reuses an id (until you wipe, which resets numbering to 1).
static uint16_t nextSessionNumber() {
    uint16_t maxNum = 0;
    File root = LittleFS.open("/");
    for (File f = root.openNextFile(); f; f = root.openNextFile()) {
        // Names look like "/s0001_hr.csv"; getName() may or may not include '/'.
        const char* n = f.name();
        const char* p = strrchr(n, '/');
        if (p) n = p + 1;
        if (n[0] == 's') {
            uint16_t num = (uint16_t)atoi(n + 1);
            if (num > maxNum) maxNum = num;
        }
    }
    return maxNum + 1;
}

static bool flashHasRoom() {
    return (LittleFS.totalBytes() - LittleFS.usedBytes()) > FLASH_MIN_FREE;
}

// Open a fresh session (new sequential id + two CSV files with headers).
static void openSession() {
    if (!flashHasRoom()) {
        flashFull = true;
        Serial.println("[REC] Flash full — not starting a session");
        return;
    }
    sessionNum = nextSessionNumber();
    char path[24];
    snprintf(path, sizeof(path), "/s%04u_hr.csv", sessionNum);
    hrFile = LittleFS.open(path, FILE_WRITE);
    snprintf(path, sizeof(path), "/s%04u_acc.csv", sessionNum);
    accFile = LittleFS.open(path, FILE_WRITE);
    if (!hrFile || !accFile) {
        Serial.println("[REC] Failed to open session files");
        if (hrFile)  hrFile.close();
        if (accFile) accFile.close();
        return;
    }
    hrFile.println("t_ms,bpm,rr_ms");
    accFile.printf("# sample_rate_hz=%d range_g=%d\n", ACC_SAMPLE_RATE, ACC_RANGE_G);
    accFile.println("t_ms,x,y,z");
    hrFile.flush();
    accFile.flush();
    sessionActive   = true;
    sessionStart_ms = millis();
    flashFull       = false;
    Serial.printf("[REC] Session %04u opened\n", sessionNum);
}

// Finalize the open session (flush + close both files).
static void closeSession() {
    if (!sessionActive) return;
    if (hrFile)  { hrFile.flush();  hrFile.close();  }
    if (accFile) { accFile.flush(); accFile.close(); }
    Serial.printf("[REC] Session %04u closed\n", sessionNum);
    sessionActive = false;
}

// Drain the HR queue into the open session file.
static void flushHR() {
    if (!sessionActive || !hrFile) return;
    HRReading r;
    int count = 0;
    while (xQueueReceive(hrQueue, &r, 0) == pdTRUE) {
        hrFile.printf("%lu,%u,", (unsigned long)(r.t_ms - sessionStart_ms), r.bpm);
        for (int i = 0; i < r.rr_count; i++) {
            if (i) hrFile.print(';');
            hrFile.print(r.rr_ms[i], 1);
        }
        hrFile.print('\n');
        count++;
    }
    if (count) hrFile.flush();
}

// Drain the ACC queue into the open session file.
static void flushACC() {
    if (!sessionActive || !accFile) return;
    ACCSample s;
    int count = 0;
    while (xQueueReceive(accQueue, &s, 0) == pdTRUE) {
        accFile.printf("%lu,%d,%d,%d\n",
                       (unsigned long)(s.t_ms - sessionStart_ms), s.x, s.y, s.z);
        count++;
    }
    if (count) accFile.flush();
}

// Flush both queues if it's time, and enforce the flash-full guard. Called from
// the record loop; if we run out of room we close the session and latch the
// "full" LED state until data is offloaded.
static void serviceRecording() {
    static uint32_t lastHR = 0, lastACC = 0;
    uint32_t now = millis();
    if (now - lastHR >= BATCH_MS)  { lastHR = now;  flushHR();  }
    if (now - lastACC >= ACC_BATCH_MS) { lastACC = now; flushACC(); }

    if (sessionActive && !flashHasRoom()) {
        Serial.println("[REC] Flash full — stopping recording");
        closeSession();
        flashFull = true;
    }
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

    if (sessionActive) xQueueSend(hrQueue, &r, 0);
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
    if (sessionActive) xQueueSend(accQueue, &s, 0);
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

// ── Status LED ────────────────────────────────────────────────────────────────
// GPIO21, active-low (LOW = lit). See config.h for the state legend.
static constexpr int PIN_BL = PIN_STATUS_LED;

static inline void ledOn()  { digitalWrite(PIN_BL, LOW);  }
static inline void ledOff() { digitalWrite(PIN_BL, HIGH); }

// Drive the LED to reflect the current state. Non-blocking; called every loop.
//   solid            → recording (strap connected)
//   slow blink       → scanning / no strap
//   fast blink       → sync mode
//   rapid triple     → flash full, recording stopped
static void updateStatusLed() {
    static uint32_t lastTick = 0;
    static uint8_t  phase    = 0;
    uint32_t now = millis();

    if (flashFull && mode == MODE_RECORD) {
        // Three quick blinks, then a pause: pattern repeats every ~1.2 s.
        if (now - lastTick >= 120) {
            lastTick = now;
            phase = (phase + 1) % 10;               // 6 blink-halves + 4 pause slots
            digitalWrite(PIN_BL, (phase < 6 && (phase % 2 == 0)) ? LOW : HIGH);
        }
        return;
    }

    if (mode == MODE_RECORD && connected) { ledOn(); return; }  // solid

    uint32_t period = (mode == MODE_SYNC) ? 150 : 700;         // fast vs slow blink
    if (now - lastTick >= period) {
        lastTick = now;
        phase ^= 1;
        digitalWrite(PIN_BL, phase ? LOW : HIGH);
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
        disconnectedAt = millis();
        Serial.printf("[BLE] Disconnected (%d), will rescan\n", reason);
    }
};

// ── Connect to Polar, subscribe to HR + ACC, open/resume the session ─────────
static bool connectToPolar() {
    pClient = NimBLEDevice::createClient();
    pClient->setClientCallbacks(new ClientCB(), false);

    if (!pClient->connect(polarAddr)) {
        Serial.println("[BLE] Connect failed");
        NimBLEDevice::deleteClient(pClient);
        pClient = nullptr;
        return false;
    }

    // The Polar H10 only emits the PMD accelerometer stream over an encrypted link
    // (HR works in the clear, ACC does not). Bond/encrypt before touching PMD.
    pClient->secureConnection();

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

    // A session begins on connect. If we dropped only briefly (within the resume
    // window) the existing session is still open, so keep writing to it; otherwise
    // open a fresh one.
    if (!sessionActive && !flashFull) openSession();
    disconnectedAt = 0;

    // ── Start the PMD accelerometer stream (best-effort; HR still works if absent)
    accStreaming = false;
    auto* pmd = pClient->getService(PMD_SVC_UUID);
    if (pmd) {
        auto* dataChr = pmd->getCharacteristic(PMD_DATA_UUID);
        auto* ctrlChr = pmd->getCharacteristic(PMD_CTRL_UUID);
        if (dataChr && ctrlChr && dataChr->canNotify()) {
            dataChr->subscribe(true, onAccNotify);         // notifications for the sample stream
            ctrlChr->subscribe(false, onPmdControl);       // indications for the command response
            pmdCtrlChr = ctrlChr;
            if (sessionActive) {
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

// If the strap has been gone longer than the resume window, finalize the session
// so the next connect starts a new file. Brief dropouts keep the file open.
static void serviceSessionLifecycle() {
    if (sessionActive && !connected && disconnectedAt &&
        millis() - disconnectedAt >= SESSION_RESUME_MS) {
        Serial.println("[REC] Resume window elapsed — finalizing session");
        closeSession();
        disconnectedAt = 0;
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Sync mode: bring WiFi up and serve the recordings over a tiny web page.
// ─────────────────────────────────────────────────────────────────────────────
static String humanSize(size_t b) {
    char buf[24];
    if (b < 1024)              snprintf(buf, sizeof(buf), "%u B", (unsigned)b);
    else if (b < 1024 * 1024)  snprintf(buf, sizeof(buf), "%.1f KB", b / 1024.0);
    else                       snprintf(buf, sizeof(buf), "%.2f MB", b / (1024.0 * 1024.0));
    return String(buf);
}

static void handleIndex() {
    size_t total = LittleFS.totalBytes(), used = LittleFS.usedBytes();
    String html = F("<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                    "<title>Polar recordings</title>"
                    "<style>body{font:15px system-ui;margin:2rem;max-width:640px}"
                    "a{color:#06c}li{margin:.3rem 0}code{color:#555}</style>"
                    "<h2>Polar recordings</h2>");
    html += "<p><code>" + humanSize(used) + " used of " + humanSize(total) + "</code></p><ul>";
    File root = LittleFS.open("/");
    bool any = false;
    for (File f = root.openNextFile(); f; f = root.openNextFile()) {
        String name = f.name();
        if (name.startsWith("/")) name = name.substring(1);
        if (!name.startsWith("s")) continue;
        any = true;
        html += "<li><a href='/dl?f=" + name + "'>" + name + "</a> "
                "<code>" + humanSize(f.size()) + "</code></li>";
    }
    if (!any) html += "<li><i>(no recordings)</i></li>";
    html += "</ul><p><a href='/wipe' onclick=\"return confirm('Delete ALL recordings?')\">"
            "Wipe all recordings</a></p>";
    server.send(200, "text/html", html);
}

static void handleDownload() {
    String f = server.arg("f");
    if (f.indexOf('/') >= 0 || !f.startsWith("s")) { server.send(400, "text/plain", "bad name"); return; }
    String path = "/" + f;
    File file = LittleFS.open(path, FILE_READ);
    if (!file) { server.send(404, "text/plain", "not found"); return; }
    server.sendHeader("Content-Disposition", "attachment; filename=" + f);
    server.streamFile(file, "text/csv");
    file.close();
}

static void handleWipe() {
    File root = LittleFS.open("/");
    String victims[64];
    int n = 0;
    for (File f = root.openNextFile(); f && n < 64; f = root.openNextFile()) {
        String name = f.name();
        if (!name.startsWith("/")) name = "/" + name;
        if (name.startsWith("/s")) victims[n++] = name;
    }
    for (int i = 0; i < n; i++) LittleFS.remove(victims[i]);
    Serial.printf("[SYNC] Wiped %d files\n", n);
    server.sendHeader("Location", "/");
    server.send(303);
}

static void enterSyncMode() {
    Serial.println("\n[SYNC] USB detected — entering sync mode");
    // Stop recording: close any open session and drop the strap so BLE is idle.
    closeSession();
    NimBLEDevice::getScan()->stop();
    if (pClient) { pClient->disconnect(); pClient = nullptr; }
    connected = false;

    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    Serial.printf("[SYNC] Joining WiFi '%s'", SYNC_SSID);
    WiFi.begin(SYNC_SSID, SYNC_PASS);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 10000) { delay(200); Serial.print('.'); }
    if (WiFi.status() != WL_CONNECTED) {
        Serial.printf("\n[SYNC] Trying WiFi '%s'", SYNC_SSID2);
        WiFi.begin(SYNC_SSID2, SYNC_PASS2);
        t0 = millis();
        while (WiFi.status() != WL_CONNECTED && millis() - t0 < 10000) { delay(200); Serial.print('.'); }
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[SYNC] Ready — open  http://%s/\n", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("\n[SYNC] WiFi failed — check SYNC_SSID/PASS in config.h");
    }

    server.on("/",     handleIndex);
    server.on("/dl",   handleDownload);
    server.on("/wipe", handleWipe);
    server.begin();
    mode = MODE_SYNC;
}

static void exitSyncMode() {
    Serial.println("[SYNC] USB removed — resuming record mode");
    server.stop();
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    mode = MODE_RECORD;
    disconnectedAt = 0;
    // Restart the scan so we reconnect to the strap and open a new session.
    NimBLEDevice::getScan()->start(0, false);
}

// USB host connection state, debounced. With ARDUINO_USB_CDC_ON_BOOT, `Serial`
// (the USB CDC) reports true once a host has opened the port — i.e. you've
// plugged into a computer. Debounced so a flaky line doesn't thrash modes.
static bool usbConnectedDebounced() {
    static bool    state    = false;
    static bool    candidate = false;
    static uint32_t since   = 0;
    bool now = (bool)Serial;
    if (now != candidate) { candidate = now; since = millis(); }
    if (candidate != state && millis() - since > 750) state = candidate;
    return state;
}

// ─────────────────────────────────────────────────────────────────────────────
void setup() {
    if (PIN_POWER_ON >= 0) {
        pinMode(PIN_POWER_ON, OUTPUT);
        digitalWrite(PIN_POWER_ON, HIGH);
    }
    pinMode(PIN_BL, OUTPUT);
    ledOff();

    Serial.begin(115200);

    hrQueue  = xQueueCreate(QUEUE_LEN, sizeof(HRReading));
    accQueue = xQueueCreate(QUEUE_LEN_ACC, sizeof(ACCSample));

    if (!LittleFS.begin(true)) {   // format-on-fail: first boot formats the region
        Serial.println("[FS] LittleFS mount failed");
    } else {
        Serial.printf("[FS] LittleFS: %s used of %s\n",
                      humanSize(LittleFS.usedBytes()).c_str(),
                      humanSize(LittleFS.totalBytes()).c_str());
    }

    // WiFi stays off until sync mode.
    WiFi.mode(WIFI_OFF);

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

void loop() {
    // Mode switching driven by USB presence.
    bool usb = usbConnectedDebounced();
    if (usb && mode == MODE_RECORD) enterSyncMode();
    else if (!usb && mode == MODE_SYNC) exitSyncMode();

    if (mode == MODE_SYNC) {
        server.handleClient();
        updateStatusLed();
        delay(2);
        return;
    }

    // ── Record mode ──────────────────────────────────────────────────────────
    if (doConnect) {
        doConnect = false;
        connectToPolar();
    }

    // No strap: keep scanning so we can find it again.
    if (!connected && pClient == nullptr && !NimBLEDevice::getScan()->isScanning()) {
        NimBLEDevice::getScan()->start(0, false);
        Serial.println("[BLE] Restarted scan");
    }

    serviceSessionLifecycle();
    serviceRecording();
    updateStatusLed();

    delay(10);
}
