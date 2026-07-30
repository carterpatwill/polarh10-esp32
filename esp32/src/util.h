#pragma once
#include <Arduino.h>

// Human-readable byte count, used by the FS-mount log and the sync uploader.
inline String humanSize(size_t b) {
    char buf[24];
    if (b < 1024)              snprintf(buf, sizeof(buf), "%u B", (unsigned)b);
    else if (b < 1024 * 1024)  snprintf(buf, sizeof(buf), "%.1f KB", b / 1024.0);
    else                       snprintf(buf, sizeof(buf), "%.2f MB", b / (1024.0 * 1024.0));
    return String(buf);
}
