// The staff API client. The session token lives in this module's memory only — never in
// localStorage, a cookie or the URL — so a reload signs you out and an XSS bug has nothing
// persistent to steal.

let token = null;
let profile = null;
const listeners = new Set();

export const session = {
  get token() { return token; },
  get user() { return profile; },
  isAdmin() { return profile?.role === "admin"; },
  set(newToken, user) { token = newToken; profile = user; listeners.forEach((fn) => fn()); },
  clear() { token = null; profile = null; listeners.forEach((fn) => fn()); },
  onChange(fn) { listeners.add(fn); },
};

export class ApiError extends Error {
  constructor(status, body) {
    const e = body?.error || {};
    super(e.message || `Request failed (${status})`);
    this.status = status; this.code = e.code || "error"; this.details = e.details || null;
  }
}

export async function api(path, { method = "GET", body, headers = {}, raw = false } = {}) {
  const init = { method, headers: { ...headers }, credentials: "omit", referrerPolicy: "no-referrer" };
  if (token) init.headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(`/api/v1${path}`, init); }
  catch { throw new ApiError(0, { error: { message: "Cannot reach the server." } }); }
  if (raw && res.ok) return res;
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && token) session.clear();
  if (!res.ok) throw new ApiError(res.status, data);
  return { data, headers: res.headers };
}

export const get = async (path) => (await api(path)).data;
export const post = async (path, body) => (await api(path, { method: "POST", body })).data;

export function qs(obj) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== "" && v != null && v !== false) p.set(k, v === true ? "1" : v);
  const s = p.toString();
  return s ? `?${s}` : "";
}
