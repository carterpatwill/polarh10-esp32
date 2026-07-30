import { useCallback, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import SessionCard from "../components/SessionCard.jsx";
import Badge from "../components/Badge.jsx";
import { getSessions } from "../api.js";
import { BUCKET_ORDER, dayKey, fmtDate } from "../lib/format.js";
import { useSessionActions } from "../lib/useSessionActions.js";

// Training tab: sessions grouped by activity bucket (walk/jog/run/sprint/other).
function Training({ training, actions }) {
  if (!training.length)
    return <div className="empty">No training data yet. Record labeled walks/jogs/runs.</div>;
  const byBucket = {};
  for (const s of training) (byBucket[s.bucket || "other"] ??= []).push(s);
  const buckets = BUCKET_ORDER.filter((b) => byBucket[b]);
  return (
    <div className="area">
      <div className="area-note">
        Short, single-activity recordings that teach the model. Click one to see exactly what X/Y/Z do.
      </div>
      {buckets.map((b) => (
        <div className="date-group" key={b}>
          <div className="date-head"><Badge bucket={b} /> <span style={{ marginLeft: 6 }}>{byBucket[b].length} session(s)</span></div>
          {byBucket[b].map((s) => (
            <SessionCard key={s.id} session={s} badge={b} onRename={actions.rename} onDelete={actions.remove} />
          ))}
        </div>
      ))}
    </div>
  );
}

// Workouts tab: real recordings, grouped by calendar day (newest first).
function Workouts({ workouts, actions }) {
  if (!workouts.length) return <div className="empty">No workouts recorded yet.</div>;
  const byDay = {};
  for (const s of workouts) (byDay[dayKey(s.started)] ??= []).push(s);
  return (
    <div className="area">
      {Object.entries(byDay).map(([day, items]) => (
        <div className="date-group" key={day}>
          <div className="date-head">{fmtDate(day)}</div>
          {items.map((s) => (
            <SessionCard key={s.id} session={s} onRename={actions.rename} onDelete={actions.remove} />
          ))}
        </div>
      ))}
    </div>
  );
}

export default function SessionList() {
  const location = useLocation();
  const tab = location.pathname === "/training" ? "training" : "workouts";
  const [sessions, setSessions] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      setSessions(await getSessions());
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Training data (kind='train') has its own tab; everything else is a workout.
  const actions = useSessionActions({ onRenamed: load, onDeleted: load });
  const training = (sessions || []).filter((s) => s.kind === "train");
  const workouts = (sessions || []).filter((s) => s.kind !== "train");

  return (
    <section>
      <h1>Sessions <span className="sub">— Polar H10 on the Pi</span>
        <Link to="/label" style={{ float: "right", fontSize: 13, fontWeight: 600 }}>🏷 Label &amp; Train →</Link></h1>

      <div className="tabs">
        <Link to="/" className={"tab" + (tab === "workouts" ? " active" : "")}>
          Workouts <span className="count">{workouts.length || ""}</span></Link>
        <Link to="/training" className={"tab" + (tab === "training" ? " active" : "")}>
          🏋 Training data <span className="count">{training.length || ""}</span></Link>
      </div>

      {error ? <div className="empty">Couldn't load sessions: {error}</div>
        : sessions === null ? <div className="empty">Loading…</div>
        : tab === "training" ? <Training training={training} actions={actions} />
        : <Workouts workouts={workouts} actions={actions} />}
    </section>
  );
}
