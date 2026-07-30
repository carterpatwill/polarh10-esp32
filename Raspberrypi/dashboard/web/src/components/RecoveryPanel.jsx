import { fmtElapsed } from "../lib/format.js";

// Turn the session-level insights numbers into a plain-English read on recovery.
// Returns an HTML string (colored <b> tags) rendered via dangerouslySetInnerHTML;
// every value is a number we computed, so there's nothing user-controlled to escape.
function recoveryInsight(d) {
  const ins = d.insights;
  if (!ins) return "";
  const parts = [];

  if (ins.quality) {
    const tint = { excellent: "#3fb950", good: "#3fb950", fair: "#d29922", sluggish: "#f85149" }[ins.quality] || "#e6edf3";
    parts.push(`Fastest recovery <b>${ins.rate_best} bpm/min</b> `
      + `(HRR60 ${ins.best_hrr60} bpm) — <b style="color:${tint}">${ins.quality}</b> `
      + `for a resting HR of ${d.resting}.`);
  } else {
    parts.push(`Fastest recovery <b>${ins.rate_best} bpm/min</b>.`);
  }

  if (ins.n_scored >= 3) {
    const swing = Math.abs(ins.trend_pct);
    if (ins.trend === "slowing")
      parts.push(`Recovery <b style="color:#f85149">slowed ${swing}%</b> from the first `
        + `effort to the last — a sign of accumulating fatigue.`);
    else if (ins.trend === "improving")
      parts.push(`Recovery <b style="color:#3fb950">sped up ${swing}%</b> across the session `
        + `— you warmed into it.`);
    else
      parts.push(`Recovery rate held <b>steady</b> across all ${ins.n_scored} efforts `
        + `— no fatigue drop-off.`);
  }
  return parts.join(" ");
}

const cell = (v, suf = "") => (v == null ? <span className="dim">–</span> : <>{v}{suf}</>);

// Per-effort heart-rate recovery: headline cards, an insight line, and the table
// scoring every effort. Renders nothing unless there are scored events.
export default function RecoveryPanel({ rec }) {
  if (!rec || rec.error || !rec.events || !rec.events.length) return null;
  const ev = rec.events;

  const bestOf = (key, cmp) =>
    ev.map((e) => e[key]).filter((v) => v != null).reduce((a, b) => cmp(a, b), null);
  const fastRate = bestOf("slope", (a, b) => (a == null ? b : Math.max(a, b)));
  const best60 = bestOf("hrr60", (a, b) => (a == null ? b : Math.max(a, b)));
  const fastTau = bestOf("tau", (a, b) => (a == null ? b : Math.min(a, b)));
  const bestHrv = bestOf("rmssd60", (a, b) => (a == null ? b : Math.max(a, b)));

  const Card = ({ k, children }) => (
    <div className="card"><div className="k">{k}</div><div className="v">{children}</div></div>
  );

  return (
    <div className="chart-wrap">
      <h2>Recovery <span className="rec-sub">
        ⚡ {ev.length} event{ev.length > 1 ? "s" : ""} · resting {rec.resting} bpm · cyan line = recovery rate
      </span></h2>

      <div className="cards">
        <Card k="Recovery rate">{fastRate != null ? <>{fastRate} <small>bpm/min</small></> : "–"}</Card>
        <Card k="Best HRR60">{best60 != null ? `${best60} bpm` : "–"}</Card>
        <Card k="Fastest τ">{fastTau != null ? `${fastTau} s` : "–"}</Card>
        <Card k="HRV rebound">{bestHrv != null ? `${bestHrv} ms` : "–"}</Card>
      </div>

      <div className="rec-insight" dangerouslySetInnerHTML={{ __html: recoveryInsight(rec) }} />

      <table className="rec">
        <thead>
          <tr>
            <th data-tip="Recovery event number, in order">#</th>
            <th data-tip="Highest heart rate at the start of this recovery (bpm).">peak</th>
            <th data-tip="When this effort peaked — elapsed time into the session.">@time</th>
            <th data-tip="Initial recovery rate — how fast HR fell over the first minute (bpm per minute). Bigger = faster recovery.">rate</th>
            <th data-tip="Heart rate difference after 60 seconds — how many bpm HR dropped in the first minute after the peak. The clinical fitness standard.">HRR60</th>
            <th data-tip="Heart rate difference after 120 seconds — bpm dropped in the first two minutes after the peak.">HRR120</th>
            <th data-tip="Tau (τ): the time constant of the exponential decay HR settles along. Smaller = faster recovery.">τ</th>
            <th data-tip="Time until HR fell back within 10 bpm of your resting HR. A '>' means it never got there in the window.">to base</th>
            <th data-tip="RMSSD ~60s after the peak — heart-rate variability (ms). Crushed by effort, it climbs back toward your resting ~59 ms as you recover.">RMSSD@60</th>
          </tr>
        </thead>
        <tbody>
          {ev.map((e) => (
            <tr key={e.n}>
              <td><span className="rec-pill">{e.n}</span></td>
              <td><b>{e.peak_bpm}</b></td>
              <td>{fmtElapsed(e.peak_t)}</td>
              <td><b style={{ color: "#39d0d8" }}>{cell(e.slope)}</b></td>
              <td>{cell(e.hrr60)}</td>
              <td>{cell(e.hrr120)}</td>
              <td>{cell(e.tau, " s")}</td>
              <td>{e.reached ? fmtElapsed(e.to_base) : <span className="dim">&gt;{fmtElapsed(e.dur)}</span>}</td>
              <td>{cell(e.rmssd60, " ms")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
