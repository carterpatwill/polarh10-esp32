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
  // Where the average sits along the min→max range, as a 0–100% offset for the dot.
  const bpmPct = (st.min != null && st.max != null && st.max > st.min && st.avg != null)
    ? Math.max(0, Math.min(100, ((st.avg - st.min) / (st.max - st.min)) * 100))
    : 50;
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
               recovery={recovery} highlight={hotEvent} sticky={tab === "recovery"} />

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={"tab" + (tab === t.id ? " active" : "")}
                  onClick={() => { setTab(t.id); setHotEvent(null); }}>{t.label}</button>
        ))}
      </div>

      {tab === "hr" && (
        <>
          {/* The three BPM numbers are the story — group them big. Duration + sample
              counts are context, so they sit small and muted underneath. */}
          <div className="bpm-panel">
            <div className="cap">Beats per minute</div>
            {/* min ── avg dot ── max, laid out like a range/gauge bar */}
            <div className="bpm-range">
              <div className="end"><div className="n">{st.min ?? "–"}</div><div className="l">min</div></div>
              <div className="track">
                <div className="bar" />
                {st.avg != null && (
                  <div className="marker" style={{ left: `${bpmPct}%` }}>
                    <div className="avg">{st.avg} <span>avg</span></div>
                    <div className="dot" />
                  </div>
                )}
              </div>
              <div className="end"><div className="n">{st.max ?? "–"}</div><div className="l">max</div></div>
            </div>
          </div>
          <div className="hr-meta">
            <b>{fmtDur(d.duration_s)}</b> duration
            {"  ·  "}{(d.hr_total ?? 0).toLocaleString()} HR samples
            {"  ·  "}{(d.acc_total ?? 0).toLocaleString()} accelerometer samples
          </div>
        </>
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
