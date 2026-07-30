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
#include <WiFiClientSecure.h>
#include <esp_eap_client.h>            // WPA2-Enterprise (eduroam) join
#include <PubSubClient.h>
#include <LittleFS.h>
#include <time.h>                       // NTP time for reconstructing session start
#include "soc/soc.h"                    // REG_READ
#include "soc/usb_serial_jtag_reg.h"    // USB-Serial-JTAG SOF frame counter
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

// Sync-mode MQTT: TLS link to HiveMQ, over which we replay stored recordings.
static WiFiClientSecure secureClient;
static PubSubClient      mqtt(secureClient);
static bool             syncUploaded = false;   // upload pass already run this sync session
static bool usbHostPresent();                    // defined below (used by the upload driver)

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
    // Record the open time so sync can reconstruct the wall-clock start: the board
    // has no RTC, but at sync it has NTP + millis(), and now = start + elapsed.
    sessionStart_ms = millis();
    hrFile.printf("# start_ms=%lu\n", (unsigned long)sessionStart_ms);
    hrFile.println("t_ms,bpm,rr_ms");
    accFile.printf("# sample_rate_hz=%d range_g=%d\n", ACC_SAMPLE_RATE, ACC_RANGE_G);
    accFile.println("t_ms,x,y,z");
    hrFile.flush();
    accFile.flush();
    sessionActive   = true;
    flashFull       = false;
    Serial.printf("[REC] Session %04u opened\n", sessionNum);
}

static void flushHR();   // defined below; drain queued readings into the open file
static void flushACC();

// Finalize the open session: drain whatever is still queued, then close both
// files. Draining first means readings buffered in the last flush interval
// aren't lost when the strap drops or we switch to sync mode.
static void closeSession() {
    if (!sessionActive) return;
    flushHR();
    flushACC();
    if (hrFile)  { hrFile.close();  }
    if (accFile) { accFile.close(); }
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
//   solid            → recording (strap connected), or sync upload done
//   slow blink       → scanning / no strap
//   fast blink       → sync mode (searching WiFi / uploading)
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
    if (mode == MODE_SYNC && syncUploaded) { ledOn(); return; } // upload done → solid

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
// Uses a LOCAL client handle throughout: the strap can drop mid-setup and the
// onDisconnect callback (NimBLE task) nulls the global pClient, so re-reading the
// global here would dereference null. We hold our own pointer and bail out via
// isConnected() checks instead.
static ClientCB clientCb;   // one shared callbacks instance (not per-connect)

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
    // open a fresh one.
    if (!sessionActive && !flashFull) openSession();
    disconnectedAt = 0;

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
            if (sessionActive) {
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
// Sync mode: bring WiFi up and push every stored recording to the Pi over MQTT.
// ─────────────────────────────────────────────────────────────────────────────
static String humanSize(size_t b) {
    char buf[24];
    if (b < 1024)              snprintf(buf, sizeof(buf), "%u B", (unsigned)b);
    else if (b < 1024 * 1024)  snprintf(buf, sizeof(buf), "%.1f KB", b / 1024.0);
    else                       snprintf(buf, sizeof(buf), "%.2f MB", b / (1024.0 * 1024.0));
    return String(buf);
}

// How many rows we pack into one MQTT message. Kept well under the 8 KB publish
// buffer once serialized (ACC rows are the longer of the two).
static const int HR_BATCH_ROWS  = 40;
static const int ACC_BATCH_ROWS = 120;

// (Re)establish the MQTT link. Returns true once connected.
static bool mqttConnect() {
    if (mqtt.connected()) return true;
    if (WiFi.status() != WL_CONNECTED) return false;
    Serial.print("[MQTT] Connecting to "); Serial.print(MQTT_HOST); Serial.print("...");
    if (mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS)) {
        Serial.println(" connected");
        return true;
    }
    Serial.printf(" failed, rc=%d\n", mqtt.state());
    return false;
}

// Publish a session start/stop marker so the Pi opens/closes a session row and
// tags the data that follows. `label` (may be nullptr) names the session;
// `startedEpoch` (0 = unknown) is the reconstructed wall-clock start in Unix time.
static bool publishSession(const char* action, const char* label, long startedEpoch = 0) {
    String s = "{\"action\":\""; s += action; s += "\"";
    if (label && label[0]) { s += ",\"label\":\""; s += label; s += "\""; }
    if (startedEpoch > 0)  { s += ",\"started_epoch\":"; s += String(startedEpoch); }
    s += "}";
    return mqtt.publish(MQTT_TOPIC_SESSION, s.c_str());
}

// Pull the "# start_ms=NNN" marker written into an HR file's header at session
// open. Returns 0 if the file or marker is missing (older recordings).
static uint32_t readSessionStartMs(const char* hrPath) {
    File f = LittleFS.open(hrPath, FILE_READ);
    if (!f) return 0;
    String line = f.readStringUntil('\n');
    f.close();
    int p = line.indexOf("start_ms=");
    return (p < 0) ? 0 : (uint32_t)strtoul(line.c_str() + p + 9, nullptr, 10);
}

// Replay one HR CSV (t_ms,bpm,rr_ms) as batched polar/hr messages. The Pi expects
// {"readings":[{"t_ms":..,"bpm":..,"rr_ms":[..]}, ...]}.
static bool uploadHrFile(File& f) {
    String batch; int n = 0;
    auto flush = [&]() -> bool {
        if (n == 0) return true;
        String body = "{\"readings\":["; body += batch; body += "]}";
        bool ok = mqtt.publish(MQTT_TOPIC, body.c_str());
        mqtt.loop(); batch = ""; n = 0; delay(20);
        return ok;
    };
    while (f.available()) {
        String line = f.readStringUntil('\n'); line.trim();
        if (line.length() == 0 || line.startsWith("#") || line.startsWith("t")) continue;
        int c1 = line.indexOf(',');
        int c2 = line.indexOf(',', c1 + 1);
        if (c1 < 0) continue;
        String t   = line.substring(0, c1);
        String bpm = (c2 < 0) ? line.substring(c1 + 1) : line.substring(c1 + 1, c2);
        String rr  = (c2 < 0) ? String("") : line.substring(c2 + 1);
        String obj = "{\"t_ms\":"; obj += t; obj += ",\"bpm\":"; obj += bpm;
        if (rr.length()) { rr.replace(";", ","); obj += ",\"rr_ms\":["; obj += rr; obj += "]"; }
        obj += "}";
        if (n) batch += ",";
        batch += obj; n++;
        if (n >= HR_BATCH_ROWS || batch.length() > 3000) { if (!flush()) return false; }
    }
    return flush();
}

// Replay one ACC CSV (t_ms,x,y,z) as batched polar/acc messages. The Pi expects
// {"sample_rate_hz":25,"samples":[[t_ms,x,y,z], ...]}.
static bool uploadAccFile(File& f) {
    String first = f.readStringUntil('\n');           // "# sample_rate..." comment
    if (first.startsWith("#")) f.readStringUntil('\n'); // then the "t_ms,x,y,z" header
    String batch; int n = 0;
    auto flush = [&]() -> bool {
        if (n == 0) return true;
        String body = "{\"sample_rate_hz\":"; body += ACC_SAMPLE_RATE;
        body += ",\"range_g\":"; body += ACC_RANGE_G;
        body += ",\"samples\":["; body += batch; body += "]}";
        bool ok = mqtt.publish(MQTT_TOPIC_ACC, body.c_str());
        mqtt.loop(); batch = ""; n = 0; delay(20);
        return ok;
    };
    while (f.available()) {
        String line = f.readStringUntil('\n'); line.trim();
        if (line.length() == 0 || line.startsWith("#") || line.startsWith("t")) continue;
        if (n) batch += ",";
        batch += "["; batch += line; batch += "]";    // line is already "t_ms,x,y,z"
        n++;
        if (n >= ACC_BATCH_ROWS || batch.length() > 3000) { if (!flush()) return false; }
    }
    return flush();
}

// Upload one session (both CSVs) to the Pi, then delete the files on success. A
// failed upload leaves the files in place so the next plug-in retries.
static void uploadSession(uint16_t num) {
    char hrPath[24], accPath[24];
    snprintf(hrPath,  sizeof(hrPath),  "/s%04u_hr.csv",  num);
    snprintf(accPath, sizeof(accPath), "/s%04u_acc.csv", num);

    bool haveHr  = LittleFS.exists(hrPath);
    bool haveAcc = LittleFS.exists(accPath);
    if (!haveHr && !haveAcc) return;

    // Reconstruct the wall-clock start from NTP: start = now − (how long ago it
    // opened). Needs a valid NTP time and no reboot since recording (a reboot
    // resets millis() below the stored value); otherwise send 0 and let the Pi
    // fall back to sync-receipt time.
    long startedEpoch = 0;
    time_t nowEpoch   = time(nullptr);
    uint32_t startMs  = readSessionStartMs(hrPath);
    if (nowEpoch > 1700000000L && startMs != 0 && startMs <= millis())
        startedEpoch = (long)nowEpoch - (long)((millis() - startMs) / 1000UL);

    Serial.printf("[SYNC] Uploading session %04u...\n", num);
    if (!mqttConnect()) { Serial.println("[SYNC] MQTT not connected — aborting"); return; }

    // No label: the dashboard names the session by its start time until you rename it.
    if (!publishSession("start", nullptr, startedEpoch)) { Serial.println("[SYNC] session start failed"); return; }
    delay(250); mqtt.loop();

    bool ok = true;
    if (haveHr)        { File f = LittleFS.open(hrPath,  FILE_READ); ok = uploadHrFile(f);  f.close(); }
    if (ok && haveAcc) { File f = LittleFS.open(accPath, FILE_READ); ok = uploadAccFile(f); f.close(); }

    delay(100); mqtt.loop();
    publishSession("stop", nullptr);
    delay(100); mqtt.loop();

    if (ok) {
        if (haveHr)  LittleFS.remove(hrPath);
        if (haveAcc) LittleFS.remove(accPath);
        Serial.printf("[SYNC] Session %04u uploaded and cleared\n", num);
    } else {
        Serial.printf("[SYNC] Session %04u upload incomplete — kept for retry\n", num);
    }
}

// Find every stored session on flash and upload each in order (oldest first).
static void runUploads() {
    uint16_t nums[64]; int cnt = 0;
    File root = LittleFS.open("/");
    for (File f = root.openNextFile(); f && cnt < 64; f = root.openNextFile()) {
        const char* n = f.name();
        const char* p = strrchr(n, '/'); if (p) n = p + 1;
        if (n[0] != 's') continue;
        uint16_t num = (uint16_t)atoi(n + 1);
        bool seen = false;
        for (int i = 0; i < cnt; i++) if (nums[i] == num) { seen = true; break; }
        if (!seen) nums[cnt++] = num;
    }
    if (cnt == 0) { Serial.println("[SYNC] No recordings to upload"); return; }
    for (int i = 0; i < cnt; i++)        // ascending order
        for (int j = i + 1; j < cnt; j++)
            if (nums[j] < nums[i]) { uint16_t t = nums[i]; nums[i] = nums[j]; nums[j] = t; }

    Serial.printf("[SYNC] %d session(s) to upload\n", cnt);
    for (int i = 0; i < cnt; i++) {
        if (!usbHostPresent()) { Serial.println("[SYNC] USB removed mid-upload — stopping"); break; }
        uploadSession(nums[i]);
        updateStatusLed();
    }
    Serial.println("[SYNC] Upload pass complete");
}

// Fast-blink the LED while we wait for an association so a plug-in shows activity
// immediately (this runs before the loop's updateStatusLed() takes over). Returns
// true once connected, false after `timeoutMs`.
static bool waitForWifi(uint32_t timeoutMs) {
    static bool ledPhase = false;
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) {
        delay(150); Serial.print('.');
        ledPhase = !ledPhase; digitalWrite(PIN_BL, ledPhase ? LOW : HIGH);
    }
    return WiFi.status() == WL_CONNECTED;
}

// Join a WPA2-Enterprise network (eduroam: PEAP/TTLS with username + password).
// Credentials live in config.h (ENT_*). On failure we disable enterprise mode so
// the plain-WPA2 fallbacks that follow aren't left in an enterprise config.
static bool joinEnterprise(uint32_t timeoutMs) {
    Serial.printf("[SYNC] Joining WPA2-Enterprise '%s' as %s", ENT_SSID, ENT_IDENTITY);
    WiFi.disconnect(true);
    esp_eap_client_set_identity((const uint8_t*)ENT_IDENTITY, strlen(ENT_IDENTITY));
    esp_eap_client_set_username((const uint8_t*)ENT_USER, strlen(ENT_USER));
    esp_eap_client_set_password((const uint8_t*)ENT_PASS, strlen(ENT_PASS));
    esp_wifi_sta_enterprise_enable();
    WiFi.begin(ENT_SSID);
    if (waitForWifi(timeoutMs)) return true;
    esp_wifi_sta_enterprise_disable();
    return false;
}

// Join a plain WPA2-PSK network.
static bool joinWpa2(const char* ssid, const char* pass, uint32_t timeoutMs) {
    Serial.printf("\n[SYNC] Trying WiFi '%s'", ssid);
    WiFi.begin(ssid, pass);
    return waitForWifi(timeoutMs);
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
    // eduroam (WPA2-Enterprise) is tried first for the dump, then the personal
    // WPA2 networks as fallbacks.
    bool joined = joinEnterprise(15000)
               || joinWpa2(SYNC_SSID,  SYNC_PASS,  10000)
               || joinWpa2(SYNC_SSID2, SYNC_PASS2, 10000);

    if (joined) {
        Serial.printf("\n[SYNC] WiFi up, IP %s — pushing recordings to the Pi\n",
                      WiFi.localIP().toString().c_str());
        // Grab NTP time so each session's real start can be reconstructed from millis().
        // UTC epoch (offset 0); the Pi renders it in local time. Wait briefly for a fix.
        configTime(0, 0, "pool.ntp.org", "time.nist.gov");
        struct tm tmNow;
        for (int i = 0; i < 20 && !getLocalTime(&tmNow, 200); i++) { /* up to ~4s */ }
        Serial.printf("[SYNC] NTP time: %ld\n", (long)time(nullptr));
        secureClient.setInsecure();          // HiveMQ TLS; no cert pinning (matches old build)
        mqtt.setServer(MQTT_HOST, MQTT_PORT);
        mqtt.setBufferSize(8192);            // batches are large; PubSubClient defaults to 256
        mqttConnect();
    } else {
        Serial.println("\n[SYNC] WiFi failed — check ENT_*/SYNC_SSID/PASS in config.h");
    }

    syncUploaded = false;   // the loop runs one upload pass once the link is up
    mode = MODE_SYNC;
}

static void exitSyncMode() {
    Serial.println("[SYNC] USB removed — resuming record mode");
    if (mqtt.connected()) mqtt.disconnect();
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    mode = MODE_RECORD;
    disconnectedAt = 0;
    // Restart the scan so we reconnect to the strap and open a new session.
    NimBLEDevice::getScan()->start(0, false);
}

// USB host presence via the USB-Serial-JTAG SOF frame counter.
//
// `(bool)Serial` only reflects DTR — set when a program *opens* the port — so a
// plain plug-in with no serial monitor never tripped sync (and a dumb charger
// looked the same as a laptop). The SOF frame index instead advances every 1 ms
// whenever a real USB host has enumerated the board, and stays frozen on battery
// or a dumb charger. We sample it and treat "advancing" as host-present.
static inline uint32_t sofFrame() {
    return REG_READ(USB_SERIAL_JTAG_FRAM_NUM_REG) & USB_SERIAL_JTAG_SOF_FRAME_INDEX;
}
static bool usbHostPresent() {
    static uint32_t prev = 0, lastChange = 0, lastSample = 0;
    static bool     seeded = false, present = false;
    uint32_t now = millis();
    if (now - lastSample >= 120) {
        lastSample = now;
        uint32_t f = sofFrame();
        if (seeded && f != prev) { lastChange = now; present = true; }  // frames moving → host
        prev = f; seeded = true;
    }
    if (present && now - lastChange > 1500) present = false;            // frozen → no host
    return present;
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
    bool usb = usbHostPresent();
    if (usb && mode == MODE_RECORD) enterSyncMode();
    else if (!usb && mode == MODE_SYNC) exitSyncMode();

    if (mode == MODE_SYNC) {
        // Once WiFi + MQTT are up, run a single pass that pushes every stored
        // recording to the Pi, then idle (solid LED) until USB is removed.
        if (!syncUploaded && WiFi.status() == WL_CONNECTED && mqttConnect()) {
            runUploads();
            syncUploaded = true;
        }
        mqtt.loop();
        updateStatusLed();
        delay(5);
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
