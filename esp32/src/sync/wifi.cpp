#include "sync/wifi.h"
#include "platform/led.h"
#include "config.h"
#include <Arduino.h>
#include <WiFi.h>
#include <esp_eap_client.h>   // WPA2-Enterprise (eduroam) join

namespace syncmode {

// Fast-blink the LED while we wait for an association so a plug-in shows activity
// immediately (this runs before the loop's led::update() takes over). Returns
// true once connected, false after `timeoutMs`.
static bool waitForWifi(uint32_t timeoutMs) {
    static bool ledPhase = false;
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) {
        delay(150); Serial.print('.');
        ledPhase = !ledPhase; platform::led::set(ledPhase);
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

bool joinAny() {
    return joinEnterprise(15000)
        || joinWpa2(SYNC_SSID,  SYNC_PASS,  10000)
        || joinWpa2(SYNC_SSID2, SYNC_PASS2, 10000);
}

}  // namespace syncmode
