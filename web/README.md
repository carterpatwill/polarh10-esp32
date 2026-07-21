# Control page (MQTT-over-WebSocket)

`control.html` replaces the old ESP32 access-point + captive-portal status page.
The ESP32 no longer serves any HTTP — it only:

- publishes a status snapshot to `polar/esp_status` every ~2s, and
- listens on `polar/session_cmd` for `{"action":"start"|"stop"}`.

This page connects **straight to the HiveMQ Cloud broker from the browser** over
MQTT-over-WebSocket (wss, port 8884). No local server is required.

## Run it locally

Just open the file in a browser:

```
open web/control.html          # macOS
```

`file://` works because the page makes an outbound `wss://` connection. If your
browser is fussy about `file://`, serve the folder instead:

```
cd web && python3 -m http.server 5500
# then open http://localhost:5500/control.html
```

## Config

Credentials/topics live in the `CFG` object at the top of `control.html` and must
match `esp32/src/config.h`:

| topic              | direction        | purpose                          |
|--------------------|------------------|----------------------------------|
| `polar/esp_status` | ESP → page       | live status snapshot (~2s)       |
| `polar/session_cmd`| page → ESP       | start/stop the session           |
| `polar/session`    | ESP → Pi         | session mark (unchanged)         |
| `pi/status`        | Pi → page        | receiver heartbeat (retained)    |

> Note: the HiveMQ credentials are embedded client-side here for local use. Fine
> for a personal project on your own machine; don't host this publicly as-is.

## Where it goes next

It's a single static file, so it can later be dropped into the Pi dashboard
(served by Flask) or any static host — no code changes needed.
