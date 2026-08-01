import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import MorningHero, { Stat, placeInBand } from "../components/MorningHero.jsx";
import MonthCalendar from "../components/MonthCalendar.jsx";
import SessionCard from "../components/SessionCard.jsx";
import { getMorning, getSessions } from "../api.js";
import { dayKey, fmtDate } from "../lib/format.js";
import { useSessionActions } from "../lib/useSessionActions.js";

// Local calendar day (YYYY-MM-DD) — matches how dayKey() slices ISO timestamps.
function todayKey() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// Home = today's readiness + workouts; /day/:date is the same view for any past day.
export default function Home() {
  const { date: paramDate } = useParams();
  const today = todayKey();
  const date = paramDate || today;
  const isToday = date === today;
  const nav = useNavigate();

  // Calendar stays tucked away until you ask for it; open automatically when the
  // page is a specific past day (you got here by picking a date, so keep it handy).
  const [showCal, setShowCal] = useState(!isToday);

  const [morning, setMorning] = useState(null);
  const [sessions, setSessions] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([getMorning(), getSessions()]);
      setMorning(m);
      setSessions(s);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const actions = useSessionActions({ onRenamed: load, onDeleted: load });

  const readings = morning?.readings || [];
  const baseline = morning?.baseline || { ready: false, n: 0, need: 7 };

  // Training data has its own page; the morning HRV capture is stored as a
  // 'morning hrv'-labelled session but shown by the hero, not as a workout.
  const isMorning = (s) => (s.label || "").toLowerCase() === "morning hrv";
  const workouts = useMemo(
    () => (sessions || []).filter((s) => s.kind !== "train" && !isMorning(s)),
    [sessions]);

  const reading = readings.find((r) => r.date === date) || null;
  const dayWorkouts = workouts.filter((s) => dayKey(s.started) === date);

  // Per-day summary the calendar colors its cells from.
  const dayMap = useMemo(() => {
    const m = {};
    for (const r of readings) {
      const status = r.quality === "good" ? placeInBand(r.ln_rmssd, baseline) : null;
      (m[r.date] ??= {}).reading = { status, quality: r.quality };
    }
    for (const s of workouts) {
      const k = dayKey(s.started);
      (m[k] ??= {}).workouts = (m[k].workouts || 0) + 1;
    }
    return m;
  }, [readings, workouts, baseline]);

  if (error) return <section><div className="empty">Couldn't load: {error}</div></section>;
  if (morning === null || sessions === null)
    return <section><div className="empty">Loading…</div></section>;

  return (
    <section>
      <h1>{isToday ? "Today" : fmtDate(date)} <span className="sub">— readiness &amp; workouts</span>
        <span className="home-nav">
          <Link to="/training">Training</Link>
          <Link to="/morning">HRV trend</Link>
          <Link to="/learn">📖 Learn</Link>
        </span>
      </h1>

      {!isToday && <div className="day-back"><Link to="/">← Back to today</Link></div>}

      <MorningHero reading={reading} baseline={baseline} date={date} />

      {reading && reading.quality === "good" && (
        <div className="cards" style={{ marginTop: 14 }}>
          <Stat k="Resting HR" v={reading.hr_rest} unit="bpm" />
          <Stat k="Respiration" v={reading.resp_rate} unit="/min" />
          <Stat k="SDNN" v={reading.sdnn} unit="ms" />
          <Stat k="pNN50" v={reading.pnn50} unit="%" />
        </div>
      )}

      <div className="date-group" style={{ marginTop: 22 }}>
        <div className="date-head">{isToday ? "Today's workouts" : "Workouts"}</div>
        {dayWorkouts.length
          ? dayWorkouts.map((s) => (
              <SessionCard key={s.id} session={s} onRename={actions.rename} onDelete={actions.remove} />
            ))
          : <div className="empty" style={{ padding: "24px 0" }}>
              No workouts recorded {isToday ? "today" : "this day"}.
            </div>}
      </div>

      <div className="home-actions">
        <button className="home-btn" aria-expanded={showCal} onClick={() => setShowCal((v) => !v)}>
          📅 Browse by date <span className="chevron">{showCal ? "▾" : "▸"}</span>
        </button>
        <Link className="home-btn" to="/sessions">🗂 View all workouts →</Link>
      </div>

      {showCal && (
        <MonthCalendar dayMap={dayMap} selected={date} today={today}
          onPick={(d) => nav(d === today ? "/" : `/day/${d}`)} />
      )}
    </section>
  );
}
