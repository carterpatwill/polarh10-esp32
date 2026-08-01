import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import MorningChart from "../components/MorningChart.jsx";
import MorningHero, { STATUS, Stat, placeInBand } from "../components/MorningHero.jsx";
import { getMorning } from "../api.js";

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
        <span className="home-nav"><Link to="/">← Today</Link><Link to="/learn">📖 Learn</Link></span></h1>

      {!readings.length ? (
        <div className="empty">
          No morning readings yet. Put on the H10 in the morning with the Pi running
          <code> morning_hrv.py watch</code> — it captures automatically.
        </div>
      ) : (
        <>
          <MorningHero reading={today} baseline={baseline} date={today?.date} />

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
