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

// Time-to-baseline cell. If HR actually settled within the window, show the real
// time. If it left the window early but we have a τ decay rate, PROJECT when it
// would reach baseline (HR hits resting+10 at t = τ·ln((peak−resting)/10)) and mark
// it "~". Only when there's no τ to extrapolate from do we fall back to ">length".
function toBaseCell(e, resting) {
  if (e.reached) return fmtElapsed(e.to_base);
  if (e.tau && e.peak_bpm - resting > 10) {
    const est = e.tau * Math.log((e.peak_bpm - resting) / 10);
    return <span className="est">~{fmtElapsed(est)}</span>;
  }
  return <span className="dim">&gt;{fmtElapsed(e.dur)}</span>;
}

// Color τ on a green→yellow→red scale — smaller τ = faster recovery = greener.
// At/below GOOD it's full green, at/above BAD full red, linearly through yellow.
function tauColor(tau) {
  if (tau == null) return undefined;
  const GOOD = 50, BAD = 150;                                   // seconds
  const f = Math.max(0, Math.min(1, (tau - GOOD) / (BAD - GOOD)));
  return `hsl(${120 * (1 - f)}, 60%, 55%)`;                     // 120=green → 0=red
}

// Per-effort heart-rate recovery: headline cards, an insight line, and the table
// scoring every effort. Renders nothing unless there are scored events.
// `highlight` is the event number currently spotlighted on the HR chart, and
// `onHover(n | null)` fires as the pointer moves over the table rows so the parent
// can drive that spotlight.
export default function RecoveryPanel({ rec, highlight = null, onHover }) {
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
        ⚡ {ev.length} event{ev.length > 1 ? "s" : ""} · resting {rec.resting} bpm · cyan line = recovery rate · hover a row to spotlight it above
      </span></h2>

      <div className="cards">
        <Card k="Recovery rate">{fastRate != null ? <>{fastRate} <small>bpm/min</small></> : "–"}</Card>
        <Card k="Best HRR60">{best60 != null ? `${best60} bpm` : "–"}</Card>
        <Card k="Fastest τ">{fastTau != null ? `${fastTau} s` : "–"}</Card>
        <Card k="HRV rebound">{bestHrv != null ? `${bestHrv} ms` : "–"}</Card>
      </div>

      <div className="rec-insight" dangerouslySetInnerHTML={{ __html: recoveryInsight(rec) }} />

      <table className="rec" onMouseLeave={() => onHover?.(null)}>
        <thead>
          <tr>
            <th data-tip="Recovery event number, in order">#</th>
            <th data-tip="Highest heart rate at the start of this recovery (bpm).">peak</th>
            <th data-tip="How long this recovery lasted — from the peak until the next effort, the time cap, or the recording's end.">length of recovery</th>
            <th data-tip="Initial recovery rate — how fast HR fell over the first minute (bpm per minute). Bigger = faster recovery.">rate</th>
            <th data-tip="Heart rate difference after 60 seconds — how many bpm HR dropped in the first minute after the peak. The clinical fitness standard.">HRR60</th>
            <th data-tip="Heart rate difference after 120 seconds — bpm dropped in the first two minutes after the peak.">HRR120</th>
            <th data-tip="Tau (τ): the time constant of the exponential decay HR settles along. Smaller = faster recovery.">τ</th>
            <th data-tip="Time until HR fell back within 10 bpm of your resting HR. A '~' is projected from the τ decay rate — how long it WOULD take at this recovery rate — when HR left the window before actually getting there. A '>' means it never got there and there was no τ to project from.">time till recovered</th>
            <th data-tip="Post-effort heart-rate variability — RMSSD (ms) sampled ~60s after the peak, during cooldown. Crushed by effort, it climbs back toward your resting ~59 ms as you recover.">HRV</th>
          </tr>
        </thead>
        <tbody>
          {ev.map((e) => (
            <tr key={e.n}
                className={highlight === e.n ? "hot" : undefined}
                onMouseEnter={() => onHover?.(e.n)}>
              <td><span className="rec-pill">{e.n}</span></td>
              <td><b>{e.peak_bpm}</b></td>
              <td>{fmtElapsed(e.dur)}</td>
              <td><b style={{ color: "#39d0d8" }}>{cell(e.slope)}</b></td>
              <td>{cell(e.hrr60)}</td>
              <td>{cell(e.hrr120)}</td>
              <td style={{ color: tauColor(e.tau), fontWeight: e.tau != null ? 700 : undefined }}>{cell(e.tau, " s")}</td>
              <td>{toBaseCell(e, rec.resting)}</td>
              <td>{cell(e.rmssd60, " ms")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
