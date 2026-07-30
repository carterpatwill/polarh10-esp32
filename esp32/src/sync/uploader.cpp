#include "sync/uploader.h"
#include "platform/led.h"
#include "platform/usb_host.h"
#include "config.h"
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <LittleFS.h>
#include <time.h>   // NTP time for reconstructing session start

namespace uploader {

// TLS link to HiveMQ, over which we replay stored recordings.
static WiFiClientSecure secureClient;
static PubSubClient      mqtt(secureClient);

// How many rows we pack into one MQTT message. Kept well under the 8 KB publish
// buffer once serialized (ACC rows are the longer of the two).
static const int HR_BATCH_ROWS  = 40;
static const int ACC_BATCH_ROWS = 120;

// (Re)establish the MQTT link. Returns true once connected.
bool connect() {
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
    if (!connect()) { Serial.println("[SYNC] MQTT not connected — aborting"); return; }

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

// ── Public interface ─────────────────────────────────────────────────────────
void beginLink() {
    secureClient.setInsecure();          // HiveMQ TLS; no cert pinning (matches old build)
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setBufferSize(8192);            // batches are large; PubSubClient defaults to 256
    connect();
}

void loop()          { mqtt.loop(); }
void disconnect()    { if (mqtt.connected()) mqtt.disconnect(); }
bool isConnected()   { return mqtt.connected(); }

// Find every stored session on flash and upload each in order (oldest first).
void runUploads() {
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
        if (!platform::usb::hostPresent()) { Serial.println("[SYNC] USB removed mid-upload — stopping"); break; }
        uploadSession(nums[i]);
        platform::led::update();
    }
    Serial.println("[SYNC] Upload pass complete");
}

}  // namespace uploader
