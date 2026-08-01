// A themed ECG strip that teaches HRV: beats are placed at real RR intervals so
// the eye sees the gaps vary (or not). Draws RR brackets + beat-to-beat Δ labels
// and the three metrics computed straight from the RR array shown. Pure SVG, no deps.

const PX_PER_SEC = 150;   // shared time scale so both strips are visually comparable
const LEAD_S = 0.35;      // quiet lead-in before the first beat
const TAIL_S = 0.45;
const MARGIN_L = 55;
const MARGIN_R = 20;

const BASE_Y = 92;        // y of the ECG baseline (signal = 0)
const AMP = 66;           // px per unit amplitude (R peak = 1.0)
const BRACKET_Y = 176;    // RR interval brackets sit here
const H = 250;            // viewBox height

const g = (t, a, mu, s) => a * Math.exp(-((t - mu) ** 2) / (2 * s * s));
// One ECG beat (P, Q, R, S, T) centered on its R peak at t = 0.
const beatAt = (t) =>
  g(t, 0.12, -0.18, 0.025) + g(t, -0.16, -0.03, 0.01) + g(t, 1.0, 0, 0.01) +
  g(t, -0.30, 0.03, 0.01) + g(t, 0.30, 0.16, 0.04);

function metrics(rr) {
  const n = rr.length;
  const mean = rr.reduce((a, b) => a + b, 0) / n;
  const sdnn = Math.sqrt(rr.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1));
  const diffs = rr.slice(1).map((v, i) => v - rr[i]);
  const rmssd = Math.sqrt(diffs.reduce((a, d) => a + d * d, 0) / diffs.length);
  const pnn50 = 100 * diffs.filter((d) => Math.abs(d) > 50).length / diffs.length;
  return { sdnn: Math.round(sdnn), rmssd: Math.round(rmssd), pnn50: Math.round(pnn50) };
}

export default function HrvDiagram({ rr, color, title, subtitle }) {
  // R-peak times (s), cumulative from the lead-in.
  const peaks = [];
  let t = LEAD_S;
  for (const r of rr) { peaks.push(t); t += r / 1000; }
  const totalS = t + TAIL_S;
  const x = (s) => MARGIN_L + s * PX_PER_SEC;
  const W = x(totalS) + MARGIN_R;

  // Sample the summed beats into an SVG path.
  let d = "";
  for (let s = 0; s <= totalS; s += 0.006) {
    let sig = 0;
    for (const p of peaks) if (Math.abs(s - p) < 0.35) sig += beatAt(s - p);
    d += `${d ? "L" : "M"}${x(s).toFixed(1)} ${(BASE_Y - sig * AMP).toFixed(1)} `;
  }

  const m = metrics(rr);
  const chips = [
    { k: "SDNN", v: `${m.sdnn} ms`, note: "overall spread" },
    { k: "RMSSD", v: `${m.rmssd} ms`, note: "how jittery your heart is" },
    { k: "pNN50", v: `${m.pnn50}%`, note: "how often the jitters are big" },
  ];

  return (
    <div className="hrv-strip">
      <div className="hrv-head">
        <div className="hrv-title" style={{ color }}>{title}</div>
        <div className="hrv-sub">{subtitle}</div>
      </div>

      <svg className="hrv-svg" viewBox={`0 0 ${W.toFixed(0)} ${H}`} preserveAspectRatio="xMidYMid meet">
        <path d={d} fill="none" stroke={color} strokeWidth="1.8"
          strokeLinejoin="round" strokeLinecap="round" />

        <text x={x(LEAD_S) - 10} y={BRACKET_Y + 4} className="hrv-axis" textAnchor="end">RR</text>
        {peaks.slice(0, -1).map((p, i) => {
          const a = x(p), b = x(peaks[i + 1]), mid = (a + b) / 2;
          const delta = i > 0 ? rr[i] - rr[i - 1] : null;
          const big = delta != null && Math.abs(delta) > 50;
          return (
            <g key={i}>
              <line x1={a} y1={BASE_Y - AMP - 4} x2={a} y2={BRACKET_Y}
                className="hrv-guide" />
              <line x1={a} y1={BRACKET_Y} x2={b} y2={BRACKET_Y} className="hrv-bracket" />
              <text x={mid} y={BRACKET_Y + 16} className="hrv-rr" textAnchor="middle">{rr[i]}</text>
              {delta != null && (
                <text x={mid} y={BRACKET_Y + 30} textAnchor="middle"
                  className="hrv-delta" fill={big ? color : "var(--muted)"}
                  fontWeight={big ? 700 : 400}>
                  {delta > 0 ? "+" : ""}{delta}
                </text>
              )}
            </g>
          );
        })}
        <line x1={x(peaks[peaks.length - 1])} y1={BASE_Y - AMP - 4}
          x2={x(peaks[peaks.length - 1])} y2={BRACKET_Y} className="hrv-guide" />
      </svg>

      <div className="hrv-chips">
        {chips.map((c) => (
          <div className="hrv-chip" key={c.k} style={{ borderColor: color }}>
            <div className="hrv-chip-k">{c.k}</div>
            <div className="hrv-chip-v" style={{ color }}>{c.v}</div>
            <div className="hrv-chip-note">{c.note}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
