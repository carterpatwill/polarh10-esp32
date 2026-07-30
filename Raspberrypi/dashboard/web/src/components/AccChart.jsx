import { useEffect, useRef } from "react";
import { mkChart, activityBand } from "../lib/charts.js";
import { ACT_COLOR, fmtDur } from "../lib/format.js";

// X/Y/Z accelerometer traces + the model's activity band, plus the percentage
// summary chips below. `activity` is the /timeline payload ({segments, totals})
// or null while it loads / when unavailable.
export default function AccChart({ acc, activity }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    const ax = acc.map((r) => ({ x: r.t, y: r.x }));
    const ay = acc.map((r) => ({ x: r.t, y: r.y }));
    const az = acc.map((r) => ({ x: r.t, y: r.z }));
    const chart = mkChart(canvasRef.current, [
      { label: "X", data: ax, borderColor: "#58a6ff" },
      { label: "Y", data: ay, borderColor: "#3fb950" },
      { label: "Z", data: az, borderColor: "#d29922" },
    ], "milli-g", { plugins: [activityBand], bottomPad: 34 });
    chart.$activity = activity?.segments || null;
    chartRef.current = chart;
    return () => chart.destroy();
  }, [acc]);

  useEffect(() => {
    const c = chartRef.current;
    if (!c) return;
    c.$activity = activity?.segments || null;
    c.update();
  }, [activity]);

  const segs = activity?.segments;
  const hint = segs?.length
    ? "· colored band = model's activity guess"
    : (activity?.error ? "· activity guess unavailable" : "");

  // Percentage summary chips, biggest first.
  let chips = null;
  if (segs?.length && activity?.totals) {
    const total = Object.values(activity.totals).reduce((a, b) => a + b, 0) || 1;
    chips = Object.entries(activity.totals)
      .sort((a, b) => b[1] - a[1])
      .map(([act, secs]) => (
        <span className="act-chip" key={act}>
          <span className="dot" style={{ background: ACT_COLOR[act] || "#8b949e" }} />
          {act} <b>{Math.round((100 * secs) / total)}%</b>
          <span style={{ color: "var(--muted)" }}> · {fmtDur(Math.round(secs))}</span>
        </span>
      ));
  }

  return (
    <div className="chart-wrap">
      <h2>Accelerometer (milli-g) <span className="hint">{hint}</span>
        <span className="spacer" />
        <button className="zoom-reset" onClick={() => chartRef.current?.resetZoom()}>reset zoom</button></h2>
      <div className="chart-box"><canvas ref={canvasRef} /></div>
      {chips && <div className="act-summary">{chips}</div>}
      <div className="hint" style={{ marginTop: 8 }}>
        Scroll / pinch to zoom · drag to pan left-right · double-click to reset
      </div>
    </div>
  );
}
