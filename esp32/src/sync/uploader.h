#pragma once

// The MQTT side of sync mode: owns the TLS link to HiveMQ and replays every stored
// recording to the Pi (oldest first), deleting each session's files on success.
namespace uploader {

void beginLink();     // TLS + broker config + first connect (called on sync entry)
bool connect();       // (re)establish the MQTT link; true once connected
void loop();          // pump the MQTT client
void disconnect();
bool isConnected();
void runUploads();    // push every stored session, then stop

}  // namespace uploader
