import { useNavigate } from "react-router-dom";
import Badge from "./Badge.jsx";
import { fmtDate, fmtDur, fmtTime, sessionName } from "../lib/format.js";

// One clickable session row for the list. `badge` is an optional bucket string
// shown as a pill (used by the Training tab). Rename/delete bubble up via callbacks.
export default function SessionCard({ session: s, badge, onRename, onDelete }) {
  const nav = useNavigate();
  const stop = (e, fn) => { e.stopPropagation(); fn(); };

  return (
    <div className="session" onClick={() => nav(`/session/${s.id}`)}>
      <div className="grow">
        <div className="label">{sessionName(s)} <Badge bucket={badge} /></div>
        <div className="when">{fmtDate(s.started)} · {fmtTime(s.started)} · {fmtDur(s.duration_s)}</div>
      </div>
      <div className="metrics">
        <div className="metric"><div className="v">{s.hr_count.toLocaleString()}</div><div className="k">HR</div></div>
        <div className="metric"><div className="v">{s.acc_count.toLocaleString()}</div><div className="k">Acc</div></div>
      </div>
      <div className="actions">
        <button className="row-btn" title="Rename" onClick={(e) => stop(e, () => onRename(s))}>✏️</button>
        <button className="row-btn del" title="Delete" onClick={(e) => stop(e, () => onDelete(s))}>🗑</button>
      </div>
      <span className="chev">›</span>
    </div>
  );
}
