// The morning-readiness hero + its shared bits, used on both the Morning page and
// the Home/Day view. It renders one reading against the rolling baseline; pass
// reading=null to show a clean "no reading this morning" state for that date.
import { fmtDate } from "../lib/format.js";

export const STATUS = {
  balanced: { color: "#3fb950", word: "Balanced",
    blurb: "Your HRV is in its normal range — recovered and ready for a normal day of training." },
  low:      { color: "#f85149", word: "Under-recovered",
    blurb: "HRV is below your normal range — a sign of fatigue, stress, or poor sleep. Favor an easy day." },
  elevated: { color: "#58a6ff", word: "Elevated",
    blurb: "HRV is above your normal range — usually a good, well-recovered sign (occasionally deep fatigue if it stays very high)." },
};

// Where a single reading sits relative to the current band — used to color its dot.
export function placeInBand(ln, b) {
  if (ln == null || !b || b.mean == null || !(b.sd > 0)) return null;
  return ln < b.lo ? "low" : ln > b.hi ? "elevated" : "balanced";
}

// % change of a reading's RMSSD vs the baseline geometric mean (matches morning.py).
export function deltaPct(ln, mean) {
  if (ln == null || mean == null) return null;
  return Math.round((Math.exp(ln - mean) - 1) * 1000) / 10;
}

// Why a reading was flagged low — a clean-but-short reading is a different problem
// from a fidgety one, and shouldn't be told to "stay still".
function lowReason(r) {
  if (r && r.artifact_pct != null && r.artifact_pct <= 5)
    return "This reading was clean (no movement) but a bit short to fully trust — "
      + "aim for the full ~4 minutes so there are enough beats.";
  return "This reading was too noisy (movement) to trust. Stay still and "
    + "relaxed next time.";
}

export const Stat = ({ k, v, unit }) => (
  <div className="card"><div className="k">{k}</div>
    <div className="v">{v == null ? <span className="dim">–</span> : <>{v}{unit && <small> {unit}</small>}</>}</div></div>
);

// `reading` is that day's morning row (or null); `date` is the day being shown so
// the hero still names the date when there's no reading.
export default function MorningHero({ reading, baseline, date }) {
  const good = reading && reading.quality === "good";
  const place = good ? placeInBand(reading.ln_rmssd, baseline) : null;
  const st = place ? STATUS[place] : null;
  const building = !baseline.ready;
  const delta = good && baseline.ready ? deltaPct(reading.ln_rmssd, baseline.mean) : null;

  const status = reading?.quality === "low" ? "Low-quality reading"
    : st ? st.word
    : reading ? "No status yet"
    : "No reading";
  const blurb = reading?.quality === "low" ? lowReason(reading)
    : st ? st.blurb
    : reading ? "Take a few more resting readings to establish what 'normal' looks like for you."
    : "No resting HRV reading was captured this morning.";

  return (
    <div className="morning-hero" style={{ borderColor: st ? st.color : "#30363d" }}>
      <div className="mh-left">
        <div className="mh-status" style={{ color: st ? st.color : "#8b949e" }}>{status}</div>
        <div className="mh-date">{fmtDate(reading?.date ?? date)}</div>
        <div className="mh-blurb">{blurb}</div>
        {reading && building && (
          <div className="mh-building">
            Building your baseline — {baseline.n}/{baseline.need} good mornings.
          </div>
        )}
      </div>
      <div className="mh-right">
        <div className="mh-rmssd" style={{ color: st ? st.color : "#e6edf3" }}>
          {reading?.rmssd ?? "–"}<small>ms RMSSD</small>
        </div>
        {delta != null && (
          <div className="mh-delta" style={{ color: st ? st.color : "#8b949e" }}>
            {delta > 0 ? "+" : ""}{delta}% vs baseline
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
