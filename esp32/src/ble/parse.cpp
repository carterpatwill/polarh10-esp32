#include "ble/parse.h"
#include "record/recorder.h"
#include "types.h"
#include <Arduino.h>
#include <NimBLEDevice.h>

namespace ble {

// ── HR notification callback (runs in NimBLE task) ───────────────────────────
void onHRNotify(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
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

    record::enqueueHr(r);
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
    record::enqueueAcc(s);
}

// ── ACC notification callback (PMD data char, delta-compressed frames) ───────
void onAccNotify(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
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

}  // namespace ble
