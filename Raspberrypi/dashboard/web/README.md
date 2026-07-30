# Dashboard front-end (React + Vite)

The browser UI for the Polar H10 dashboard. **Source lives here on the Mac; the
Pi only ever gets the compiled bundle.** `npm run build` compiles this into
`../static/dist/`, which the Flask app (`../api/`) serves as static files. The Pi
never runs Node.

## Develop

```bash
npm install
npm run dev          # Vite on http://localhost:5173
```

You also need the API running so `/api/*` calls resolve (Vite proxies them):

```bash
cd ..                # Raspberrypi/dashboard
.venv/bin/python3 app.py     # Flask on :8000
```

Open http://localhost:5173. The labeler page (`#/label`) only works locally,
where `data/library_api.py` and the training library are importable.

## Build / ship

```bash
npm run build        # → ../static/dist
```

`../deploy-dashboard.sh` does this for you, then rsyncs everything **except**
`web/` (source) to the Pi and restarts the service.

## Layout

```
src/
├── main.jsx            entry — mounts <App/>, imports styles.css
├── App.jsx             HashRouter: list / detail / label
├── api.js              every fetch to the Flask JSON API
├── styles.css          merged styles (label rules scoped under .label-page)
├── lib/
│   ├── format.js       fmtDur/fmtDate/… + BUCKET_ORDER, ACT_COLOR
│   ├── charts.js       Chart.js setup + activityBand/recoveryOverlay plugins + mkChart
│   └── useSessionActions.js   rename/delete (prompt/confirm) shared by list + detail
├── components/
│   ├── SessionCard.jsx  Badge.jsx
│   ├── HrChart.jsx  AccChart.jsx  RecoveryPanel.jsx
│   └── Tooltip.jsx      global [data-tip] hover tooltip
└── pages/
    ├── SessionList.jsx   Workouts + Training tabs
    ├── SessionDetail.jsx  cards + charts + recovery
    └── Label.jsx          labeler (Mac-only)
```
