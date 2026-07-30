// Small formatting helpers shared across views (ported 1:1 from the old inline JS).

export function fmtDur(s) {
  if (s == null) return "–";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? h + "h " : "") + (m ? m + "m " : "") + sec + "s";
}

export function fmtElapsed(sec) {
  sec = Math.round(sec);
  const m = Math.floor(sec / 60), s = sec % 60;
  return m + ":" + String(s).padStart(2, "0");
}

export function fmtDate(iso) {
  // A date-only string ("YYYY-MM-DD") is parsed as UTC midnight, which shifts
  // the day back in negative-offset timezones. Append a time so it parses as local.
  const d = iso.length === 10 ? new Date(iso + "T00:00:00") : new Date(iso);
  return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" });
}

export function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Compact date + time, e.g. "7/28/26, 7:47 PM" — the default session name.
export function fmtDateTime(iso) {
  return new Date(iso).toLocaleString([], {
    month: "numeric", day: "numeric", year: "2-digit", hour: "numeric", minute: "2-digit",
  });
}

// The name to show for a session: the user's custom label, else its start date/time.
// (JSX escapes on render, so unlike the old code this returns a plain string.)
export function sessionName(s) {
  return s.label ? s.label : fmtDateTime(s.started);
}

export function dayKey(iso) { return iso.slice(0, 10); }  // YYYY-MM-DD

export const BUCKET_ORDER = ["walk", "jog", "run", "sprint", "other"];

// Colors for the activity timeline (match the bucket badges; still = grey).
export const ACT_COLOR = {
  walk: "#58a6ff", jog: "#3fb950", run: "#d29922", sprint: "#f85149", still: "#8b949e", other: "#8b949e",
};
