#include "record/recorder.h"
#include "config.h"
#include "util.h"
#include <Arduino.h>
#include <LittleFS.h>

namespace record {

static QueueHandle_t hrQueue;
static QueueHandle_t accQueue;

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

// ── Public interface ─────────────────────────────────────────────────────────
void begin() {
    hrQueue  = xQueueCreate(QUEUE_LEN, sizeof(HRReading));
    accQueue = xQueueCreate(QUEUE_LEN_ACC, sizeof(ACCSample));

    if (!LittleFS.begin(true)) {   // format-on-fail: first boot formats the region
        Serial.println("[FS] LittleFS mount failed");
    } else {
        Serial.printf("[FS] LittleFS: %s used of %s\n",
                      humanSize(LittleFS.usedBytes()).c_str(),
                      humanSize(LittleFS.totalBytes()).c_str());
    }
}

void enqueueHr(const HRReading& r) {
    if (sessionActive) xQueueSend(hrQueue, &r, 0);
}

void enqueueAcc(const ACCSample& s) {
    if (sessionActive) xQueueSend(accQueue, &s, 0);
}

void openIfNeeded() {
    if (!sessionActive && !flashFull) openSession();
    disconnectedAt = 0;   // (re)connected: cancel any pending finalize
}

void notifyDisconnected() {
    disconnectedAt = millis();
}

// Flush both queues if it's time, and enforce the flash-full guard. Called from
// the record loop; if we run out of room we close the session and latch the
// "full" LED state until data is offloaded.
void service() {
    static uint32_t lastHR = 0, lastACC = 0;
    uint32_t now = millis();
    if (now - lastHR >= BATCH_MS)  { lastHR = now;  flushHR();  }
    if (now - lastACC >= ACC_BATCH_MS) { lastACC = now; flushACC(); }

    if (sessionActive && !flashHasRoom()) {
        Serial.println("[REC] Flash full — stopping recording");
        close();
        flashFull = true;
    }
}

// If the strap has been gone longer than the resume window, finalize the session
// so the next connect starts a new file. Brief dropouts keep the file open.
void serviceLifecycle(bool connected) {
    if (sessionActive && !connected && disconnectedAt &&
        millis() - disconnectedAt >= SESSION_RESUME_MS) {
        Serial.println("[REC] Resume window elapsed — finalizing session");
        close();
        disconnectedAt = 0;
    }
}

// Finalize the open session: drain whatever is still queued, then close both
// files. Draining first means readings buffered in the last flush interval
// aren't lost when the strap drops or we switch to sync mode.
void close() {
    if (!sessionActive) return;
    flushHR();
    flushACC();
    if (hrFile)  { hrFile.close();  }
    if (accFile) { accFile.close(); }
    Serial.printf("[REC] Session %04u closed\n", sessionNum);
    sessionActive = false;
}

bool isActive()    { return sessionActive; }
bool isFlashFull() { return flashFull; }

}  // namespace record
