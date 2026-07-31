import { useEffect, useRef } from "react";
import { Chart } from "../lib/charts.js";

// Draws the "normal range" as a shaded horizontal band behind the RMSSD line, plus
// a dashed mean line. Reads chart.$band = { lo, hi, mean } in RMSSD ms (already
// converted from the lnRMSSD baseline). Skipped until there's a band to show.
const baselineBand = {
  id: "baselineBand",
  beforeDatasetsDraw(chart) {
    const b = chart.$band;
    if (!b) return;
    const { ctx, chartArea: a, scales } = chart;
    const ys = scales.y;
    const yHi = ys.getPixelForValue(b.hi), yLo = ys.getPixelForValue(b.lo);
    ctx.save();
    ctx.fillStyle = "rgba(63,185,80,.10)";                 // faint green normal-range zone
    ctx.fillRect(a.left, yHi, a.right - a.left, yLo - yHi);
    ctx.strokeStyle = "rgba(63,185,80,.45)";
    ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
    const yM = ys.getPixelForValue(b.mean);
    ctx.beginPath(); ctx.moveTo(a.left, yM); ctx.lineTo(a.right, yM); ctx.stroke();
    ctx.restore();
  },
};

const STATUS_COLOR = { balanced: "#3fb950", low: "#f85149", elevated: "#58a6ff" };

// RMSSD (ms) per morning, over the baseline band. Each point is colored by that
// morning's status; low-quality readings are drawn hollow so they don't read as
// trustworthy. RMSSD (not lnRMSSD) on the axis because it's the number you feel.
export default function MorningChart({ readings, baseline }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    const labels = readings.map((r) => r.date.slice(5));   // MM-DD
    const data = readings.map((r) => r.rmssd);
    const ptColor = readings.map((r) =>
      r.quality === "good" ? (STATUS_COLOR[r.baselineStatus] || "#8b949e") : "#6e7681");
    const ptStyle = readings.map((r) => (r.quality === "good" ? "circle" : "crossRot"));

    const chart = new Chart(canvasRef.current, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "RMSSD (ms)", data,
          borderColor: "#e6edf3", borderWidth: 1.5, tension: 0.25,
          pointRadius: 5, pointHoverRadius: 7,
          pointBackgroundColor: ptColor, pointBorderColor: ptColor, pointStyle: ptStyle,
          spanGaps: true,
        }],
      },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        scales: {
          x: { grid: { color: "rgba(139,148,158,.15)" } },
          y: { grid: { color: "rgba(139,148,158,.15)" }, title: { display: true, text: "RMSSD (ms)" },
               beginAtZero: false },
        },
        plugins: { legend: { display: false } },
      },
      plugins: [baselineBand],
    });
    // Convert the lnRMSSD baseline to RMSSD ms for the band overlay. Set before an
    // explicit update() — with animation off, the chart's one render already happened
    // in the constructor, so the plugin wouldn't otherwise see the band.
    chart.$band = baseline && baseline.mean != null && baseline.sd > 0
      ? { lo: Math.exp(baseline.lo), hi: Math.exp(baseline.hi), mean: Math.exp(baseline.mean) }
      : null;
    chart.update();
    chartRef.current = chart;
    return () => chart.destroy();
  }, [readings, baseline]);

  return <div className="chart-box morning-trend"><canvas ref={canvasRef} /></div>;
}
