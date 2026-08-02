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

// ── Delete-gate acknowledgement ───────────────────────────────────────────────
// The Pi confirms a stored session on MQTT_TOPIC_ACK: {"uid":N,"hr":H,"acc":A}.
// We only delete a session file once we've seen an ack for its uid whose row
// counts cover what we sent. These hold the most recent ack while we wait.
static uint32_t ackUid    = 0;      // uid of the session we're currently awaiting
static bool     ackGot    = false;  // an ack for ackUid arrived
static long     ackHrRows = -1;     // rows the Pi reports storing (that session)
static long     ackAccRows = -1;

// Pull a signed integer that follows "key": in a JSON blob. Returns `dflt` if the
// key is absent. Tiny hand-parse — no JSON lib on the upload path.
static long jsonLong(const char* s, const char* key, long dflt) {
    const char* p = strstr(s, key);
    if (!p) return dflt;
    p += strlen(key);
    while (*p == ' ' || *p == ':' || *p == '"') p++;
    return strtol(p, nullptr, 10);
}

// MQTT subscribe callback — only the ack topic matters here.
static void onMqtt(char* topic, byte* payload, unsigned int len) {
    if (strcmp(topic, MQTT_TOPIC_ACK) != 0) return;
    char buf[96];
    unsigned n = len < sizeof(buf) - 1 ? len : sizeof(buf) - 1;
    memcpy(buf, payload, n); buf[n] = '\0';
    uint32_t uid = (uint32_t)jsonLong(buf, "\"uid\"", 0);
    if (uid != ackUid || uid == 0) return;    // ack for a different/earlier session
    ackHrRows  = jsonLong(buf, "\"hr\"",  -1);
    ackAccRows = jsonLong(buf, "\"acc\"", -1);
    ackGot     = true;
}

// A recording's stable upload id: same bytes on flash → same uid, so a retry of a
// file the Pi already stored is recognised as the same session (no duplicate) and
// re-acked. Derived from the session number + its recorded start_ms (FNV-1a).
static uint32_t makeUid(uint16_t num, uint32_t startMs) {
    uint32_t h = 2166136261u;
    uint8_t bytes[6] = { (uint8_t)num, (uint8_t)(num >> 8),
                         (uint8_t)startMs, (uint8_t)(startMs >> 8),
                         (uint8_t)(startMs >> 16), (uint8_t)(startMs >> 24) };
    for (uint8_t b : bytes) { h ^= b; h *= 16777619u; }
    return h ? h : (num ? num : 1u);    // never 0 (reserved "no ack" sentinel)
}

// (Re)establish the MQTT link. Returns true once connected.
bool connect() {
    if (mqtt.connected()) return true;
    if (WiFi.status() != WL_CONNECTED) return false;
    Serial.print("[MQTT] Connecting to "); Serial.print(MQTT_HOST); Serial.print("...");
    if (mqtt.connect(MQTT_CLIENT_ID, MQTT_USER, MQTT_PASS)) {
        Serial.println(" connected");
        mqtt.subscribe(MQTT_TOPIC_ACK);   // (re)subscribe so deletes stay gated on the ack
        return true;
    }
    Serial.printf(" failed, rc=%d\n", mqtt.state());
    return false;
}

// Publish a session start/stop marker so the Pi opens/closes a session row and
// tags the data that follows. `label` (may be nullptr) names the session;
// `startedEpoch` (0 = unknown) is the reconstructed wall-clock start in Unix time.
static bool publishSession(const char* action, const char* label, uint32_t uid,
                           long startedEpoch = 0) {
    String s = "{\"action\":\""; s += action; s += "\"";
    s += ",\"uid\":"; s += String(uid);
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

// Replay one HR CSV (t_ms,bpm,rr_ms) as batched polar/hr messages, each tagged
// with `uid` so the Pi can attribute it even if the start marker was lost. The Pi
// expects {"uid":N,"readings":[{"t_ms":..,"bpm":..,"rr_ms":[..]}, ...]}.
// Returns the number of rows sent, or -1 if a publish failed (link trouble).
static long uploadHrFile(File& f, uint32_t uid) {
    String batch; int n = 0; long sent = 0;
    auto flush = [&]() -> bool {
        if (n == 0) return true;
        String body = "{\"uid\":"; body += String(uid); body += ",\"readings\":[";
        body += batch; body += "]}";
        bool ok = mqtt.publish(MQTT_TOPIC, body.c_str());
        mqtt.loop(); batch = ""; sent += n; n = 0; delay(20);
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
        if (n >= HR_BATCH_ROWS || batch.length() > 3000) { if (!flush()) return -1; }
    }
    return flush() ? sent : -1;
}

// Replay one ACC CSV (t_ms,x,y,z) as batched polar/acc messages tagged with `uid`.
// The Pi expects {"uid":N,"sample_rate_hz":25,"samples":[[t_ms,x,y,z], ...]}.
// Returns the number of samples sent, or -1 if a publish failed.
static long uploadAccFile(File& f, uint32_t uid) {
    String first = f.readStringUntil('\n');           // "# sample_rate..." comment
    if (first.startsWith("#")) f.readStringUntil('\n'); // then the "t_ms,x,y,z" header
    String batch; int n = 0; long sent = 0;
    auto flush = [&]() -> bool {
        if (n == 0) return true;
        String body = "{\"uid\":"; body += String(uid);
        body += ",\"sample_rate_hz\":"; body += ACC_SAMPLE_RATE;
        body += ",\"range_g\":"; body += ACC_RANGE_G;
        body += ",\"samples\":["; body += batch; body += "]}";
        bool ok = mqtt.publish(MQTT_TOPIC_ACC, body.c_str());
        mqtt.loop(); batch = ""; sent += n; n = 0; delay(20);
        return ok;
    };
    while (f.available()) {
        String line = f.readStringUntil('\n'); line.trim();
        if (line.length() == 0 || line.startsWith("#") || line.startsWith("t")) continue;
        if (n) batch += ",";
        batch += "["; batch += line; batch += "]";    // line is already "t_ms,x,y,z"
        n++;
        if (n >= ACC_BATCH_ROWS || batch.length() > 3000) { if (!flush()) return -1; }
    }
    return flush() ? sent : -1;
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
    uint32_t startMs  = readSessionStartMs(hrPath);
    long startedEpoch = 0;
    time_t nowEpoch   = time(nullptr);
    if (nowEpoch > 1700000000L && startMs != 0 && startMs <= millis())
        startedEpoch = (long)nowEpoch - (long)((millis() - startMs) / 1000UL);

    uint32_t uid = makeUid(num, startMs);   // stable across retries → no duplicates

    Serial.printf("[SYNC] Uploading session %04u (uid=%08X)...\n", num, uid);
    if (!connect()) { Serial.println("[SYNC] MQTT not connected — aborting"); return; }

    // Arm the ack listener for this uid before we send anything.
    ackUid = uid; ackGot = false; ackHrRows = -1; ackAccRows = -1;

    // No label: the dashboard names the session by its start time until you rename it.
    if (!publishSession("start", nullptr, uid, startedEpoch)) { Serial.println("[SYNC] session start failed"); return; }
    delay(250); mqtt.loop();

    long hrSent = 0, accSent = 0;
    if (haveHr)  { File f = LittleFS.open(hrPath,  FILE_READ); hrSent  = uploadHrFile(f, uid);  f.close(); }
    if (haveAcc && hrSent >= 0) { File f = LittleFS.open(accPath, FILE_READ); accSent = uploadAccFile(f, uid); f.close(); }
    if (hrSent < 0 || accSent < 0) {   // a publish failed mid-stream — keep the file, retry next sync
        Serial.printf("[SYNC] Session %04u send failed — kept for retry\n", num);
        return;
    }

    delay(100); mqtt.loop();
    publishSession("stop", nullptr, uid);

    // Wait for the Pi to confirm it stored at least what we sent. Only then is it
    // safe to erase the file. No ack (or short counts) → keep it and retry later.
    bool confirmed = false;
    uint32_t t0 = millis();
    while (millis() - t0 < ACK_TIMEOUT_MS) {
        mqtt.loop();
        if (ackGot && ackHrRows >= hrSent && ackAccRows >= accSent) { confirmed = true; break; }
        delay(20);
    }

    if (confirmed) {
        if (haveHr)  LittleFS.remove(hrPath);
        if (haveAcc) LittleFS.remove(accPath);
        Serial.printf("[SYNC] Session %04u confirmed (hr %ld/%ld, acc %ld/%ld) — cleared\n",
                      num, ackHrRows, hrSent, ackAccRows, accSent);
    } else if (ackGot) {
        Serial.printf("[SYNC] Session %04u ack short (hr %ld/%ld, acc %ld/%ld) — kept for retry\n",
                      num, ackHrRows, hrSent, ackAccRows, accSent);
    } else {
        Serial.printf("[SYNC] Session %04u no ack within %d ms — kept for retry\n", num, ACK_TIMEOUT_MS);
    }
}

// ── Public interface ─────────────────────────────────────────────────────────
void beginLink() {
    secureClient.setInsecure();          // HiveMQ TLS; no cert pinning (matches old build)
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setBufferSize(8192);            // batches are large; PubSubClient defaults to 256
    mqtt.setCallback(onMqtt);            // receive the Pi's per-session delete acks
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
