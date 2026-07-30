import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Badge from "../components/Badge.jsx";
import HrChart from "../components/HrChart.jsx";
import AccChart from "../components/AccChart.jsx";
import RecoveryPanel from "../components/RecoveryPanel.jsx";
import { getRecovery, getSession, getTimeline } from "../api.js";
import { fmtDate, fmtDur, fmtTime, sessionName } from "../lib/format.js";
import { useSessionActions } from "../lib/useSessionActions.js";

function Card({ k, children }) {
  return <div className="card"><div className="k">{k}</div><div className="v">{children}</div></div>;
}

const TABS = [
  { id: "hr", label: "Heart rate" },
  { id: "recovery", label: "Recovery" },
  { id: "motion", label: "Motion" },
];

export default function SessionDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const [d, setD] = useState(null);
  const [activity, setActivity] = useState(null);   // /timeline payload or null
  const [recovery, setRecovery] = useState(null);   // /recovery payload or null
  const [notFound, setNotFound] = useState(false);
  const [tab, setTab] = useState("hr");             // which panel shows under the HR chart
  const [hotEvent, setHotEvent] = useState(null);   // recovery event spotlighted on the chart

  // Reload the base session (after a rename) without refetching the traces.
  async function loadDetail() {
    const data = await getSession(id);
    if (data.error) { setNotFound(true); return; }
    setD(data);
  }

  useEffect(() => {
    let alive = true;
    setD(null); setActivity(null); setRecovery(null); setNotFound(false);
    setTab("hr"); setHotEvent(null);
    window.scrollTo(0, 0);

    getSession(id).then((data) => {
      if (!alive) return;
      if (data.error) { setNotFound(true); return; }
      setD(data);
    });
    // Model overlays arrive async and independently; either may be unavailable.
    getTimeline(id).then((t) => alive && setActivity(t)).catch(() => {});
    getRecovery(id).then((r) => alive && setRecovery(r)).catch(() => {});
    return () => { alive = false; };
  }, [id]);

  const actions = useSessionActions({
    onRenamed: loadDetail,
    onDeleted: () => nav("/"),
  });

  if (notFound) return (
    <section><Link className="back" to="/">← All sessions</Link><h1>Session not found</h1></section>
  );
  if (!d) return (
    <section><Link className="back" to="/">← All sessions</Link><div className="empty">Loading…</div></section>
  );

  const st = d.stats || {};
  return (
    <section>
      <Link className="back" to="/">← All sessions</Link>
      <h1>
        {sessionName(d)}
        {d.kind === "train" && <> <Badge bucket={d.bucket || "other"}>training · {d.bucket || "other"}</Badge></>}
        {"  ·  "}{fmtDate(d.started)} {fmtTime(d.started)}
        <button className="rename-btn" onClick={() => actions.rename(d)} title="Rename this session">✏️ Rename</button>
        <button className="rename-btn del" onClick={() => actions.remove(d)} title="Delete this session">🗑 Delete</button>
      </h1>

      {/* The HR chart stays put on top — tabs below only swap what's shown, so the
          curve is a stable backdrop the recovery hover can spotlight against. */}
      <HrChart hr={d.hr} activity={activity?.segments || null}
               recovery={recovery} highlight={hotEvent} />

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={"tab" + (tab === t.id ? " active" : "")}
                  onClick={() => { setTab(t.id); setHotEvent(null); }}>{t.label}</button>
        ))}
      </div>

      {tab === "hr" && (
        <div className="cards">
          <Card k="Duration">{fmtDur(d.duration_s)}</Card>
          <Card k="Avg BPM">{st.avg ?? "–"}</Card>
          <Card k="Min BPM">{st.min ?? "–"}</Card>
          <Card k="Max BPM">{st.max ?? "–"}</Card>
          <Card k="HR samples">{(d.hr_total ?? 0).toLocaleString()}</Card>
          <Card k="Acc samples">{(d.acc_total ?? 0).toLocaleString()}</Card>
        </div>
      )}

      {tab === "recovery" && (
        recovery == null
          ? <div className="empty">Loading recovery…</div>
          : (recovery.error || !recovery.events?.length)
            ? <div className="empty">No recovery events in this session — needs a jog/run effort followed by a walk/still cooldown.</div>
            : <RecoveryPanel rec={recovery} highlight={hotEvent} onHover={setHotEvent} />
      )}

      {tab === "motion" && <AccChart acc={d.acc} activity={activity} />}
    </section>
  );
}
