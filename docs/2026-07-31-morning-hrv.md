# Morning HRV readiness baseline (2026-07-31)

Get a resting HRV reading first thing every morning, hands-off, and track it against
a rolling baseline so you can see whether you're recovered or run down.

## The idea

The Pi lives in the bedroom, so let *it* be the Bluetooth central — skip the ESP32
entirely for this. During a morning window (default 07:00–11:00) the Pi watches for
the Polar H10. The moment you put the strap on, it connects, records a few quiet
minutes of beat-to-beat (RR) intervals, computes the metrics, and files one row per
morning. All you did was put the strap on.

**Why RR off the standard HR service (not the PMD/ACC link):** resting HRV only needs
the beat-to-beat intervals, which the H10 broadcasts on the plain, un-encrypted Heart
Rate Measurement characteristic (`0x2A37`). No secure PMD channel, no accelerometer —
none of the ACC-on-XIAO pairing saga. Just subscribe and read.

## Pieces (mirrors the producer/consumer split)

- **`Raspberrypi/server/morning_hrv.py`** — producer. BLE capture daemon + CLI.
  Writes the raw trace into `sessions`/`hr` (like a recorded workout, `kind='metric'`,
  label `morning hrv`) and a summary row into a new **`morning_hrv`** table.
  Commands: `watch` (the service), `once` (capture now), `backfill <session>`
  (compute from an existing still recording), `list`, `baseline`.
  Deps: `bleak` + `numpy` only.
- **`Raspberrypi/server/morning_hrv.service`** — systemd unit that runs `watch`.
- **`Raspberrypi/dashboard/api/morning.py`** — consumer. `/api/morning` returns the
  readings + the rolling baseline + today's status. Pure reader.
- **`web/src/pages/Morning.jsx`** + `components/MorningChart.jsx` — the `#/morning`
  page: readiness hero, resting-HR/respiration/SDNN/pNN50 cards, an RMSSD trend with
  the normal-range band shaded, and a readings table. Linked from the Sessions header.

## Metrics (all from the cleaned RR series)

RR cleaning mirrors `data/recovery.py` so the numbers agree: keep 300–1500 ms, drop
>20% successive jumps (ectopic/motion). Then:

- **RMSSD** and **lnRMSSD** — lnRMSSD is what the baseline is built on (log RMSSD is
  ~normal, so a mean ± SD band behaves).
- **Resting HR** — from median RR, not the noisy bpm field.
- **SDNN**, **pNN50**.
- **Respiration rate** — from respiratory sinus arrhythmia: rebuild the beat clock
  from cumulative RR, FFT, take the dominant peak in the 0.15–0.40 Hz band
  (9–24 breaths/min). numpy-only, no scipy.
- **Quality** — `good` needs ≥60 clean beats and ≤5% rejected; else `low`. Movement
  spikes the rejection rate, which is our stand-in for "were you actually still"
  (the Pi has no accelerometer view). Low readings are stored but excluded from the
  baseline band.

The first `WARMUP_SECONDS` (60 s) of each capture are discarded (settling in).

### Validation

`backfill 36` on the "resting heart rate" session reproduced the known anchor
exactly: **RMSSD 59.0 ms, resting HR 54, 138 beats, `good`** — matching the
2026-07-25 measurement (RMSSD ≈ 59, HR ≈ 53). The metric math is correct.

## Rolling baseline / readiness

`api/morning.py._baseline`: last 60 days of **good** readings → mean lnRMSSD ± 1 SD is
your "normal range". Today lands:

- inside → **balanced** (recovered, train normally)
- below → **under-recovered** (fatigue/stress/poor sleep — favor easy)
- above → **elevated** (usually good; occasionally deep fatigue if very high)

Flagged "still building" until ≥7 good mornings.

## Migrating to the Pi

Onboard BT was disabled (`dtoverlay=disable-bt`, freeing the UART for a now-unused
ESP32 serial link). **Already re-enabled** 2026-07-31: commented that line out in
`/boot/firmware/config.txt` and rebooted → `hci0` is up. (Backup at
`config.txt.bak.*`; re-add the line to revert.)

Remaining steps:

1. Deploy `morning_hrv.py` + `morning_hrv.service` to `~/projects/python/esp-polar/server/`.
2. `./.venv/bin/pip install bleak numpy` in the server venv.
3. Test: `./.venv/bin/python3 morning_hrv.py once --duration 60` with the strap on.
4. Install the service (fill `__USER__`/`__DIR__`, `systemctl enable --now morning-hrv`).
5. Rebuild the dashboard front-end and redeploy so `#/morning` ships.

## Known limitations / future

- Stillness is inferred from RR artifact rate, not motion — a very still but stressed
  morning still reads low HRV (correct); a fidgety one is flagged `low`.
- One capture per calendar day (first good reading wins).
- Respiration is an estimate from RR, not a chest-band airflow measurement.
- Consider: sleep-time trend, CV-based "smallest worthwhile change" band, a compact
  readiness chip on the Sessions home.
