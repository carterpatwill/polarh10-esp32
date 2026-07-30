import { useEffect, useRef } from "react";
import { mkChart, activityBand, recoveryOverlay } from "../lib/charts.js";

// Heart-rate line with the activity band + recovery overlay plugins. Rebuilds when
// the HR series changes; the activity/recovery overlays update the live chart in
// place (so arriving async without a full redraw), matching the old behavior.
export default function HrChart({ hr, activity, recovery }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    const data = hr.map((r) => ({ x: r.t, y: r.bpm }));
    const chart = mkChart(canvasRef.current, [
      { label: "BPM", data, borderColor: "#f85149", fill: true, backgroundColor: "rgba(248,81,73,.08)" },
    ], "BPM", { plugins: [activityBand, recoveryOverlay], bottomPad: 34 });
    chart.$activity = activity || null;
    chart.$recovery = recovery || null;
    chartRef.current = chart;
    return () => chart.destroy();
  }, [hr]);

  useEffect(() => {
    const c = chartRef.current;
    if (!c) return;
    c.$activity = activity || null;
    c.update();
  }, [activity]);

  useEffect(() => {
    const c = chartRef.current;
    if (!c) return;
    c.$recovery = recovery || null;
    c.update();
  }, [recovery]);

  return (
    <div className="chart-wrap">
      <h2>Heart rate (BPM) <span className="spacer" />
        <button className="zoom-reset" onClick={() => chartRef.current?.resetZoom()}>reset zoom</button></h2>
      <div className="chart-box"><canvas ref={canvasRef} /></div>
    </div>
  );
}
