import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import MorningChart from "../components/MorningChart.jsx";
import { getMorning } from "../api.js";
import { fmtDate } from "../lib/format.js";

const STATUS = {
  balanced: { color: "#3fb950", word: "Balanced",
    blurb: "Your HRV is in its normal range — recovered and ready for a normal day of training." },
  low:      { color: "#f85149", word: "Under-recovered",
    blurb: "HRV is below your normal range — a sign of fatigue, stress, or poor sleep. Favor an easy day." },
  elevated: { color: "#58a6ff", word: "Elevated",
    blurb: "HRV is above your normal range — usually a good, well-recovered sign (occasionally deep fatigue if it stays very high)." },
};

// Where a single reading sits relative to the current band — used to color its dot.
function placeInBand(ln, b) {
  if (ln == null || !b || b.mean == null || !(b.sd > 0)) return null;
  return ln < b.lo ? "low" : ln > b.hi ? "elevated" : "balanced";
}

// Why a reading was flagged low — a clean-but-short reading is a different problem
// from a fidgety one, and shouldn't be told to "stay still".
function lowReason(today) {
  if (today && today.artifact_pct != null && today.artifact_pct <= 5)
    return "This reading was clean (no movement) but a bit short to fully trust — "
      + "aim for the full ~4 minutes so there are enough beats.";
  return "This morning's reading was too noisy (movement) to trust. Stay still and "
    + "relaxed next time.";
}

const Stat = ({ k, v, unit }) => (
  <div className="card"><div className="k">{k}</div>
    <div className="v">{v == null ? <span className="dim">–</span> : <>{v}{unit && <small> {unit}</small>}</>}</div></div>
);

function Hero({ today, baseline }) {
  const st = today && today.quality === "good" && baseline.status ? STATUS[baseline.status] : null;
  const building = !baseline.ready;

  return (
    <div className="morning-hero" style={{ borderColor: st ? st.color : "#30363d" }}>
      <div className="mh-left">
        <div className="mh-status" style={{ color: st ? st.color : "#8b949e" }}>
          {today?.quality === "low" ? "Low-quality reading"
            : st ? st.word : "No status yet"}
        </div>
        <div className="mh-date">{today ? fmtDate(today.date) : "—"}</div>
        <div className="mh-blurb">
          {today?.quality === "low"
            ? lowReason(today)
            : st ? st.blurb
            : "Take a few resting readings to establish what 'normal' looks like for you."}
        </div>
        {building && (
          <div className="mh-building">
            Building your baseline — {baseline.n}/{baseline.need} good mornings.
          </div>
        )}
      </div>
      <div className="mh-right">
        <div className="mh-rmssd" style={{ color: st ? st.color : "#e6edf3" }}>
          {today?.rmssd ?? "–"}<small>ms RMSSD</small>
        </div>
        {baseline.delta_pct != null && baseline.ready && (
          <div className="mh-delta" style={{ color: st ? st.color : "#8b949e" }}>
            {baseline.delta_pct > 0 ? "+" : ""}{baseline.delta_pct}% vs baseline
          </div>
        )}
        {baseline.mean != null && (
          <div className="mh-baseline">
            normal ≈ {Math.round(Math.exp(baseline.mean))} ms
            {baseline.sd > 0 && <> ({Math.round(Math.exp(baseline.lo))}–{Math.round(Math.exp(baseline.hi))})</>}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Morning() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getMorning().then(setData).catch((e) => setError(e.message));
  }, []);

  if (error) return <section><div className="empty">Couldn't load morning HRV: {error}</div></section>;
  if (data === null) return <section><div className="empty">Loading…</div></section>;

  const { readings, baseline, today } = data;
  // Newest→oldest for the table; oldest→newest (with per-point status) for the chart.
  const withStatus = readings.map((r) => ({ ...r, baselineStatus: placeInBand(r.ln_rmssd, baseline) }));
  const table = [...withStatus].reverse();

  return (
    <section>
      <h1>Morning readiness <span className="sub">— resting HRV baseline</span>
        <Link to="/" style={{ float: "right", fontSize: 13, fontWeight: 600 }}>← Sessions</Link></h1>

      {!readings.length ? (
        <div className="empty">
          No morning readings yet. Put on the H10 in the morning with the Pi running
          <code> morning_hrv.py watch</code> — it captures automatically.
        </div>
      ) : (
        <>
          <Hero today={today} baseline={baseline} />

          <div className="cards" style={{ marginTop: 14 }}>
            <Stat k="Resting HR" v={today?.hr_rest} unit="bpm" />
            <Stat k="Respiration" v={today?.resp_rate} unit="/min" />
            <Stat k="SDNN" v={today?.sdnn} unit="ms" />
            <Stat k="pNN50" v={today?.pnn50} unit="%" />
          </div>

          <div className="chart-wrap" style={{ marginTop: 16 }}>
            <h2>RMSSD trend <span className="rec-sub">
              green band = your normal range · dot color = that day's status · ✕ = low-quality
            </span></h2>
            <MorningChart readings={withStatus} baseline={baseline} />
          </div>

          <div className="chart-wrap" style={{ marginTop: 16 }}>
            <h2>Readings</h2>
            <table className="rec">
              <thead>
                <tr>
                  <th>date</th><th>RMSSD</th><th>rest HR</th><th>resp</th>
                  <th>SDNN</th><th>pNN50</th><th>beats</th><th>quality</th>
                </tr>
              </thead>
              <tbody>
                {table.map((r) => {
                  const c = r.quality === "good" ? STATUS[r.baselineStatus]?.color : "#6e7681";
                  return (
                    <tr key={r.date}>
                      <td>{r.session ? <Link to={`/session/${r.session}`}>{r.date}</Link> : r.date}</td>
                      <td><b style={{ color: c }}>{r.rmssd ?? "–"}</b></td>
                      <td>{r.hr_rest ?? "–"}</td>
                      <td>{r.resp_rate ?? <span className="dim">–</span>}</td>
                      <td>{r.sdnn ?? "–"}</td>
                      <td>{r.pnn50 ?? "–"}</td>
                      <td>{r.beats}</td>
                      <td>{r.quality === "good"
                        ? <span style={{ color: "#3fb950" }}>good</span>
                        : <span className="dim">low</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
