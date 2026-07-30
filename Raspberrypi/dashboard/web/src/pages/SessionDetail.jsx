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

export default function SessionDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const [d, setD] = useState(null);
  const [activity, setActivity] = useState(null);   // /timeline payload or null
  const [recovery, setRecovery] = useState(null);   // /recovery payload or null
  const [notFound, setNotFound] = useState(false);

  // Reload the base session (after a rename) without refetching the traces.
  async function loadDetail() {
    const data = await getSession(id);
    if (data.error) { setNotFound(true); return; }
    setD(data);
  }

  useEffect(() => {
    let alive = true;
    setD(null); setActivity(null); setRecovery(null); setNotFound(false);
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

      <div className="cards">
        <Card k="Duration">{fmtDur(d.duration_s)}</Card>
        <Card k="Avg BPM">{st.avg ?? "–"}</Card>
        <Card k="Min BPM">{st.min ?? "–"}</Card>
        <Card k="Max BPM">{st.max ?? "–"}</Card>
        <Card k="HR samples">{(d.hr_total ?? 0).toLocaleString()}</Card>
        <Card k="Acc samples">{(d.acc_total ?? 0).toLocaleString()}</Card>
      </div>

      <HrChart hr={d.hr} activity={activity?.segments || null} recovery={recovery} />
      <AccChart acc={d.acc} activity={activity} />
      <RecoveryPanel rec={recovery} />
    </section>
  );
}
