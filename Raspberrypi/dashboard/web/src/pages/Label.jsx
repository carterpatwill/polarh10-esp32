import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  getLabelStatus, getLabelCandidates, getLabelLibrary,
  labelAdd, labelRemove, labelTrain,
} from "../api.js";

const pct = (x) => (x == null ? "—" : (x * 100).toFixed(0) + "%");

// Intensity histogram with the kept [t0,t1) window highlighted amber.
function Bars({ intensity, t0, t1 }) {
  if (!intensity.length) return <div className="meta">no motion data</div>;
  const max = Math.max(...intensity, 1);
  return (
    <div className="bars">
      {intensity.map((v, i) => {
        const h = Math.max(3, Math.round((v / max) * 48));
        const keep = i >= t0 && i < t1 ? " keep" : "";
        return <div className={"bar" + keep} style={{ height: h }} title={`${i}s: ${v}`} key={i} />;
      })}
    </div>
  );
}

function CandidateCard({ s, buckets, onAdded }) {
  const [t0, setT0] = useState(s.trim.t0);
  const [t1, setT1] = useState(s.trim.t1);
  const [bucket, setBucket] = useState(s.suggested_bucket || "");
  const [status, setStatus] = useState(null);   // {kind:'ok'|'err', text}
  const [done, setDone] = useState(s.already_added);
  const [busy, setBusy] = useState(false);

  async function add() {
    if (!bucket) { setStatus({ kind: "err", text: "pick a label first" }); return; }
    setBusy(true); setStatus({ kind: "", text: "adding…" });
    const { ok, data } = await labelAdd({ id: s.id, bucket, t0, t1 });
    if (ok) {
      setStatus({ kind: "ok", text: `✓ added ${data.kept_seconds}s as ${data.bucket}` });
      setDone(true);
      onAdded();
    } else {
      setStatus({ kind: "err", text: data.error || "failed" });
      setBusy(false);
    }
  }

  return (
    <div className={"card" + (done ? " done" : "")}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h3>#{s.id} · {s.label ?? <span className="meta">no label</span>}</h3>
          <div className="meta">{s.duration_s}s recorded{done ? " · already in library" : ""}</div>
        </div>
      </div>

      <Bars intensity={s.intensity} t0={t0} t1={t1} />
      <div className="axis"><span>0s</span><span>keep = amber</span><span>{s.duration_s}s</span></div>

      {done ? (
        <div className="status">Already added — retrain to include it, or remove it from the Library above.</div>
      ) : (
        <div className="ctl">
          <label className="f">trim</label>
          <input className="t" type="number" min="0" value={t0} onChange={(e) => setT0(+e.target.value)} />
          <span className="meta">→</span>
          <input className="t" type="number" min="0" value={t1} onChange={(e) => setT1(+e.target.value)} />
          <span className="meta">s</span>
          <span className="seg">
            {buckets.map((b) => (
              <button key={b} className={b + (bucket === b ? " on " + b : "")} onClick={() => setBucket(b)}>{b}</button>
            ))}
          </span>
          <button className="act" disabled={busy} onClick={add}>Add to library</button>
          {status && <span className={"status " + status.kind}>{status.text}</span>}
        </div>
      )}
    </div>
  );
}

function TrainResult({ data }) {
  if (!data) return null;
  if (data.error) return <div className="status err" style={{ marginTop: 10 }}>{data.error}</div>;
  return (
    <>
      {data.scored ? (
        <>
          <div className="row" style={{ gap: 16, marginTop: 14, alignItems: "baseline" }}>
            <span className="acc">{pct(data.accuracy)}</span>
            <span className="meta">honest accuracy — each session guessed while held out</span>
          </div>
          <table className="rep">
            <tbody>
              <tr><th>class</th><th>precision</th><th>recall</th><th>f1</th><th>n</th></tr>
              {data.buckets.map((b) => {
                const m = data.per_class[b] || {};
                return (
                  <tr key={b}>
                    <td>{b}</td><td>{pct(m.precision)}</td><td>{pct(m.recall)}</td>
                    <td>{pct(m.f1)}</td><td>{m.support ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="meta" style={{ marginTop: 12 }}>Confusion — rows = actual, cols = guessed</div>
          <table className="rep cm">
            <tbody>
              <tr><th></th>{data.confusion.labels.map((l) => <th key={l}>{l}</th>)}</tr>
              {data.confusion.matrix.map((r, i) => (
                <tr key={i}>
                  <td>{data.confusion.labels[i]}</td>
                  {r.map((v, j) => (
                    <td key={j} className={i === j ? "diag" : v ? "off" : ""}>{v}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <div className="status" style={{ marginTop: 10 }}>{data.note || "trained"}</div>
      )}
      {data.empty?.length > 0 && (
        <div className="status" style={{ marginTop: 8 }}>Empty buckets: {data.empty.join(", ")} — record some.</div>
      )}
    </>
  );
}

export default function Label() {
  const [available, setAvailable] = useState(null);   // null = checking
  const [lib, setLib] = useState(null);
  const [cands, setCands] = useState(null);
  const [buckets, setBuckets] = useState(["walk", "jog", "run"]);
  const [trainOut, setTrainOut] = useState(null);
  const [training, setTraining] = useState(false);

  async function loadLib() { setLib(await getLabelLibrary()); }
  async function loadCands() {
    const c = await getLabelCandidates();
    setCands(c);
    if (c.buckets) setBuckets(c.buckets);
  }

  useEffect(() => {
    getLabelStatus().then(async ({ available }) => {
      setAvailable(available);
      if (available) { await loadLib(); await loadCands(); }
    });
  }, []);

  async function remove(id) {
    const { ok } = await labelRemove(id);
    if (ok) { await loadLib(); await loadCands(); }
  }

  async function train() {
    setTraining(true);
    const { data } = await labelTrain();
    setTraining(false);
    setTrainOut(data);
  }

  const header = (
    <div className="row" style={{ justifyContent: "space-between" }}>
      <div>
        <h1>Label &amp; Train</h1>
        <p className="sub">Add new recordings to the training library, auto-trimmed, then retrain — no terminal.</p>
      </div>
      <Link to="/">← Sessions</Link>
    </div>
  );

  if (available === null) return <>{header}</>;
  if (!available) return (
    <>
      {header}
      <div className="panel" style={{ color: "var(--red)" }}>
        Labeler unavailable — this page only works when the dashboard runs locally on the Mac
        (next to <code>data/library_api.py</code> and the training library).
      </div>
    </>
  );

  const total = lib?.total ?? 0;
  const fresh = (cands?.sessions || []).filter((s) => !s.already_added);
  const candNote = cands?.error
    ? cands.error
    : cands ? `${fresh.length} new · ${cands.sessions.length} total in the latest dump` : "";

  return (
    <>
      {header}

      <div className="panel">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>
              Library <span className="meta">· {total} sessions</span>
            </div>
            <div className="lib">
              {buckets.map((b) => {
                const items = lib?.buckets?.[b] || [];
                const secs = items.reduce((a, x) => a + x.duration_s, 0);
                return (
                  <span className={"chip " + b} key={b}>
                    <b>{b}</b> {items.length} · {secs}s{" "}
                    {items.map((x) => (
                      <button className="ghost" title={`remove #${x.id}`} key={x.id} onClick={() => remove(x.id)}>#{x.id} ✕</button>
                    ))}
                  </span>
                );
              })}
            </div>
          </div>
          <button id="trainBtn" className="act" disabled={training} onClick={train}>
            {training ? "Training…" : "Retrain model"}
          </button>
        </div>
        <TrainResult data={trainOut} />
      </div>

      <div style={{ fontWeight: 600, margin: "18px 0 8px" }}>New recordings</div>
      <div className="sub">{candNote}</div>
      <div>
        {(cands?.sessions || []).map((s) => (
          <CandidateCard key={s.id} s={s} buckets={buckets} onAdded={loadLib} />
        ))}
      </div>
    </>
  );
}
