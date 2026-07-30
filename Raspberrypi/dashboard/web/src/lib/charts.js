// Chart.js setup + the two custom canvas plugins (activity band, recovery overlay)
// and the shared line-chart factory. Ported verbatim from the old inline dashboard;
// the only change is npm imports + registration instead of CDN globals.
import { Chart, registerables } from "chart.js";
import zoomPlugin from "chartjs-plugin-zoom";
import Hammer from "hammerjs";

import { ACT_COLOR, fmtElapsed } from "./format.js";

// chartjs-plugin-zoom's pinch support looks for a global Hammer.
if (typeof window !== "undefined") window.Hammer = Hammer;

Chart.register(...registerables, zoomPlugin);
Chart.defaults.color = "#8b949e";
Chart.defaults.font.size = 11;

const GRID = "rgba(139,148,158,.15)";

// Draws the model's activity segments as a colored band under the chart, with a
// dashed divider line at each transition timestamp and a label per segment.
// Reads chart.$activity = [{t0,t1,activity}]; needs ~34px of bottom padding.
export const activityBand = {
  id: "activityBand",
  afterDatasetsDraw(chart) {
    const segs = chart.$activity;
    if (!segs || !segs.length) return;
    const { ctx, chartArea: a, scales } = chart;
    const xs = scales.x, bandTop = a.bottom + 8, bandH = 16;
    ctx.save();
    ctx.font = "700 9px -apple-system, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const s of segs) {
      const x0 = Math.max(a.left, xs.getPixelForValue(s.t0));
      const x1 = Math.min(a.right, xs.getPixelForValue(s.t1));
      if (x1 <= x0) continue;
      const col = ACT_COLOR[s.activity] || "#8b949e";
      // colored band segment
      ctx.globalAlpha = 0.9; ctx.fillStyle = col;
      ctx.fillRect(x0, bandTop, x1 - x0, bandH);
      // divider line up through the plot at the transition timestamp
      ctx.globalAlpha = 0.22; ctx.strokeStyle = col; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x0, a.top); ctx.lineTo(x0, bandTop + bandH); ctx.stroke();
      ctx.setLineDash([]);
      // label if the segment is wide enough to read
      if (x1 - x0 > 30) {
        ctx.globalAlpha = 1; ctx.fillStyle = "#0d1117";
        ctx.fillText(s.activity.toUpperCase(), (x0 + x1) / 2, bandTop + bandH / 2 + 0.5);
      }
    }
    ctx.restore();
  },
};

// Draws recovery events onto the HR chart: shaded cooldown windows, the fitted
// exponential decay curve, and a dot + number at each peak. Reads chart.$recovery.
export const recoveryOverlay = {
  id: "recoveryOverlay",
  afterDatasetsDraw(chart) {
    const rec = chart.$recovery;
    if (!rec || !rec.overlay) return;
    const { ctx, chartArea: a, scales } = chart;
    const xs = scales.x, ys = scales.y;
    const clampX = (t) => Math.max(a.left, Math.min(a.right, xs.getPixelForValue(t)));
    ctx.save();
    // shaded cooldown windows
    ctx.fillStyle = "#f85149";
    for (const w of rec.overlay.windows) {
      const x0 = clampX(w.t0), x1 = clampX(w.t1);
      if (x1 <= x0) continue;
      ctx.globalAlpha = 0.07; ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
    }
    // recovery decay: solid = trustworthy exponential τ fit; dashed = straight
    // peak→low guide (a real drop that was too noisy for a clean fit).
    const fitted = rec.overlay.decay_fitted || [];
    ctx.globalAlpha = 0.9; ctx.strokeStyle = "#f0b429"; ctx.lineWidth = 1.6;
    rec.overlay.decays.forEach((curve, ci) => {
      if (!curve || !curve.length) return;
      ctx.setLineDash(fitted[ci] === false ? [5, 4] : []);
      ctx.beginPath();
      curve.forEach((p, i) => {
        const x = xs.getPixelForValue(p.t), y = ys.getPixelForValue(p.bpm);
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    });
    ctx.setLineDash([]);
    // initial recovery RATE: a straight cyan line = the bpm/min slope we report,
    // laid over the first minute of the fall so you can see the speed you're graded on.
    ctx.globalAlpha = 0.95; ctx.strokeStyle = "#39d0d8"; ctx.lineWidth = 2.2;
    for (const s of (rec.overlay.slopes || [])) {
      if (!s) continue;
      ctx.beginPath();
      ctx.moveTo(xs.getPixelForValue(s.t0), ys.getPixelForValue(s.bpm0));
      ctx.lineTo(xs.getPixelForValue(s.t1), ys.getPixelForValue(s.bpm1));
      ctx.stroke();
    }
    // peak dots + numbers
    ctx.font = "700 10px -apple-system, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    for (const pk of rec.overlay.peaks) {
      const x = xs.getPixelForValue(pk.t), y = ys.getPixelForValue(pk.bpm);
      if (x < a.left || x > a.right) continue;
      ctx.globalAlpha = 1; ctx.fillStyle = "#f85149";
      ctx.beginPath(); ctx.arc(x, y, 4, 0, 2 * Math.PI); ctx.fill();
      ctx.fillStyle = "#e6edf3"; ctx.fillText("#" + pk.n, x, y - 6);
    }
    ctx.restore();
  },
};

// Build a line chart on `canvas`. `opts.plugins` are per-chart canvas plugins;
// `opts.bottomPad` reserves room under the plot for the activity band.
export function mkChart(canvas, datasets, yTitle, opts = {}) {
  const chart = new Chart(canvas, {
    type: "line",
    data: { datasets },
    plugins: opts.plugins || [],
    options: {
      animation: false, parsing: false, normalized: true,
      responsive: true, maintainAspectRatio: false,
      layout: { padding: { bottom: opts.bottomPad || 0 } },
      interaction: { mode: "nearest", axis: "x", intersect: false },
      elements: { point: { radius: 0 }, line: { borderWidth: 1.5, tension: 0.2 } },
      scales: {
        x: {
          type: "linear", grid: { color: GRID },
          ticks: { callback: (v) => fmtElapsed(v) },
          title: { display: true, text: "elapsed (min:sec)" },
        },
        y: { grid: { color: GRID }, title: { display: !!yTitle, text: yTitle } },
      },
      plugins: {
        legend: { display: datasets.length > 1, labels: { boxWidth: 12 } },
        tooltip: { callbacks: { title: (items) => fmtElapsed(items[0].parsed.x) } },
        zoom: {
          pan: { enabled: true, mode: "x" },                        // drag to scroll left/right
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" },
          limits: { x: { min: "original", max: "original" } },      // can't scroll past the data
        },
      },
    },
  });
  canvas.ondblclick = () => chart.resetZoom();
  return chart;
}
