import { useState } from "react";
import { STATUS } from "./MorningHero.jsx";

// One month grid for navigating to a day. `dayMap` is keyed by "YYYY-MM-DD" with
// { status, quality, workouts } summaries; the caller decides where a pick goes.
const WD = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

function ymd(y, m, d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${y}-${p(m + 1)}-${p(d)}`;
}

// The dot color for a day's morning reading: grey if low-quality, band color if
// good, muted if good-but-baseline-not-yet-established.
function dotColor(reading) {
  if (!reading) return null;
  if (reading.quality !== "good") return "#6e7681";
  return reading.status ? STATUS[reading.status].color : "#8b949e";
}

export default function MonthCalendar({ dayMap, selected, today, onPick }) {
  const [sy, sm] = selected.split("-").map(Number);
  const [view, setView] = useState({ y: sy, m: sm - 1 });

  const startWd = new Date(view.y, view.m, 1).getDay();
  const daysIn = new Date(view.y, view.m + 1, 0).getDate();
  // Don't let you page past the current month — there's nothing there yet.
  const [ty, tm] = today.split("-").map(Number);
  const atCurrentMonth = view.y === ty && view.m === tm - 1;

  const cells = [];
  for (let i = 0; i < startWd; i++) cells.push(null);
  for (let d = 1; d <= daysIn; d++) cells.push(d);

  const step = (delta) => setView((v) => {
    const d = new Date(v.y, v.m + delta, 1);
    return { y: d.getFullYear(), m: d.getMonth() };
  });

  return (
    <div className="cal">
      <div className="cal-head">
        <button className="cal-nav" onClick={() => step(-1)} title="Previous month">‹</button>
        <div className="cal-title">{MONTHS[view.m]} {view.y}</div>
        <button className="cal-nav" onClick={() => step(1)} disabled={atCurrentMonth}
          title="Next month">›</button>
      </div>

      <div className="cal-grid">
        {WD.map((w) => <div className="cal-wd" key={w}>{w}</div>)}
        {cells.map((d, i) => {
          if (d == null) return <div className="cal-cell empty" key={`e${i}`} />;
          const key = ymd(view.y, view.m, d);
          const info = dayMap[key] || {};
          const future = key > today;
          const cls = ["cal-cell"];
          if (future) cls.push("future");
          if (key === today) cls.push("today");
          if (key === selected) cls.push("sel");
          const dot = dotColor(info.reading);
          return (
            <div className={cls.join(" ")} key={key}
              onClick={future ? undefined : () => onPick(key)}>
              {dot && <span className="cal-dot" style={{ background: dot }} />}
              <span className="cal-num">{d}</span>
              {info.workouts > 0 && (
                <span className="cal-wk" title={`${info.workouts} workout(s)`} />
              )}
            </div>
          );
        })}
      </div>

      <div className="cal-legend">
        <span><i className="cal-dot" style={{ background: STATUS.balanced.color }} /> balanced</span>
        <span><i className="cal-dot" style={{ background: STATUS.low.color }} /> under-recovered</span>
        <span><i className="cal-dot" style={{ background: STATUS.elevated.color }} /> elevated</span>
        <span><i className="cal-dot" style={{ background: "#6e7681" }} /> low quality</span>
        <span><i className="cal-wk" style={{ position: "static", transform: "none" }} /> workout</span>
      </div>
    </div>
  );
}
