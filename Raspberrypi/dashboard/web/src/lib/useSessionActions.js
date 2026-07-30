import { renameSession, deleteSession } from "../api.js";
import { fmtDateTime } from "./format.js";

// Rename / delete a session with the same native prompt/confirm flow the old
// dashboard used. `onRenamed(id, label)` and `onDeleted(id)` fire on success so
// the caller can refresh its list or leave the detail view.
export function useSessionActions({ onRenamed, onDeleted } = {}) {
  async function rename(s) {
    const cur = s.label || fmtDateTime(s.started);
    const name = prompt("Name this session (leave blank to use the date/time):", cur);
    if (name === null) return;   // cancelled
    try {
      const res = await renameSession(s.id, name);
      onRenamed?.(s.id, res.label);
    } catch (e) {
      alert("Couldn't rename: " + e.message);
    }
  }

  async function remove(s) {
    const nm = s.label || fmtDateTime(s.started);
    if (!confirm(`Delete "${nm}"?\n\nThis permanently removes the session and all of its `
      + `heart-rate and accelerometer data. This can't be undone.`)) return;
    try {
      await deleteSession(s.id);
      onDeleted?.(s.id);
    } catch (e) {
      alert("Couldn't delete: " + e.message);
    }
  }

  return { rename, remove };
}
