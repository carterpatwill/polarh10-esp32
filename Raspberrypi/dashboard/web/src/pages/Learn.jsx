import { Link } from "react-router-dom";
import HrvDiagram from "../components/HrvDiagram.jsx";
import { STATUS } from "../components/MorningHero.jsx";

// Textbook extremes so the effect is obvious — both average ~70 bpm, only the
// variability differs. The metrics in each strip are computed from these gaps.
const HEALTHY = [1010, 790, 1060, 720, 980, 760, 1030, 810];
const STRESSED = [858, 863, 855, 861, 857, 860, 856, 862];

const METRICS = [
  { k: "RMSSD", tag: "the headline", color: "#e6edf3",
    what: "The size of the jump from one beat's gap to the next, averaged over the reading.",
    more: "The gold standard for short readings. It reflects fast, moment-to-moment changes driven by your vagus nerve (the 'rest & digest' system). This is the big number on your morning screen." },
  { k: "pNN50", tag: "% of big jumps", color: "#58a6ff",
    what: "The percentage of consecutive beats whose gap changed by more than 50 ms.",
    more: "Another view of that same fast, vagal activity — it moves together with RMSSD. Higher % = more of those quick adjustments = more recovered." },
  { k: "SDNN", tag: "overall spread", color: "#d29922",
    what: "The total spread (standard deviation) of all the beat-to-beat gaps in the reading.",
    more: "The wide-angle view — it captures both the fast flickers and slower drifts across the whole ~4 minutes. Bigger = more total variability." },
  { k: "Resting HR", tag: "context", color: "#3fb950",
    what: "Your heart rate while lying still — how many beats per minute.",
    more: "HRV and resting HR usually move in opposite directions: a well-recovered morning tends to show higher HRV and a lower resting pulse." },
];

function StatusRow({ k }) {
  const s = STATUS[k];
  return (
    <div className="learn-status">
      <span className="cal-dot" style={{ position: "static", transform: "none", background: s.color }} />
      <b style={{ color: s.color }}>{s.word}</b>
      <span>{s.blurb}</span>
    </div>
  );
}

export default function Learn() {
  return (
    <section className="learn">
      <h1>Learn <span className="sub">— what your morning numbers mean</span>
        <span className="home-nav"><Link to="/">← Today</Link></span></h1>

      <div className="learn-lead">
        <b>Heart-rate variability (HRV)</b> is the small, natural variation in the time between
        your heartbeats. A plain pulse of "60 bpm" doesn't beat once every second like a clock —
        the gaps constantly wobble by a few tens of milliseconds. <b>That wobble is the signal.</b>
        A rested body produces lots of it; a stressed or fatigued one flattens out into a rigid,
        metronome-like rhythm.
      </div>

      <h2 className="learn-h2">See it: two hearts, same pulse</h2>
      <p className="learn-p">
        Both hearts below beat about 70 times a minute — identical heart rate. The only
        difference is the <b>gaps between beats</b> (RR intervals, in ms). Watch how the numbers
        underneath swing on top and barely move on the bottom.
      </p>

      <HrvDiagram rr={HEALTHY} color="#3fb950" title="Healthy · recovered"
        subtitle="Gaps swing widely — high variability. The vagus nerve is actively adjusting." />
      <HrvDiagram rr={STRESSED} color="#f85149" title="Stressed · fatigued"
        subtitle="Gaps nearly identical — low variability. A rigid, metronome-like rhythm." />

      <div className="learn-callout">
        The counterintuitive part: <b>the jagged, irregular trace is the healthy one.</b> You
        <i> want</i> variability. A steady, unchanging heartbeat is the warning sign — it means
        your recovery system has gone quiet. So for every metric here, <b>higher is better.</b>
      </div>

      <h2 className="learn-h2">The metrics on your morning card</h2>
      <div className="learn-metrics">
        {METRICS.map((m) => (
          <div className="learn-metric" key={m.k} style={{ borderLeftColor: m.color }}>
            <div className="learn-metric-head">
              <span className="learn-metric-k">{m.k}</span>
              <span className="learn-metric-tag">{m.tag}</span>
            </div>
            <div className="learn-metric-what">{m.what}</div>
            <div className="learn-metric-more">{m.more}</div>
          </div>
        ))}
      </div>

      <h2 className="learn-h2">How this app reads it</h2>
      <p className="learn-p">
        One reading on its own means little — HRV varies person to person and day to day. So the
        app tracks <b>your own</b> RMSSD over the last ~60 mornings and builds a personal
        "normal" range (the green band on the <Link to="/morning">HRV trend</Link>). Each morning
        it just asks where today sits in your band:
      </p>
      <div className="learn-statuses">
        <StatusRow k="balanced" />
        <StatusRow k="low" />
        <StatusRow k="elevated" />
      </div>
      <p className="learn-p learn-foot">
        It takes about a week of good morning readings to establish the band. Readings are most
        comparable when taken the same way each day — lying still, right after waking, before
        coffee. A short or fidgety reading gets flagged low-quality and is left out of your baseline.
      </p>
    </section>
  );
}
