// One place for every call to the Flask JSON API. In dev, Vite proxies /api to
// the Flask server (see vite.config.js); in prod they're the same origin.

async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  return { ok: r.ok, data: await r.json() };
}

// ---- sessions ----
export const getSessions = () => getJSON("/api/sessions");
export const getSession = (id) => getJSON(`/api/session/${id}`);
export const getTimeline = (id) => getJSON(`/api/session/${id}/timeline`);
export const getRecovery = (id) => getJSON(`/api/session/${id}/recovery`);

export async function renameSession(id, name) {
  const { ok, data } = await postJSON(`/api/session/${id}/name`, { name });
  if (!ok) throw new Error(data.error || "save failed");
  return data;
}

export async function deleteSession(id) {
  const r = await fetch(`/api/session/${id}`, { method: "DELETE" });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "delete failed");
  return data;
}

// ---- labeler (Mac-only) ----
export const getLabelStatus = () => getJSON("/api/label/status");
export const getLabelCandidates = () => getJSON("/api/label/candidates");
export const getLabelLibrary = () => getJSON("/api/label/library");
export const labelAdd = (body) => postJSON("/api/label/add", body);
export const labelRemove = (id) => postJSON("/api/label/remove", { id });
export const labelTrain = () => postJSON("/api/label/train", {});
