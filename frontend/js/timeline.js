// A usage timeline drawn as SVG: one bar per session, sessions that overlap are stacked in
// separate lanes. Colour is never the only signal: a faulty report also gets a ✗ glyph and
// a text label in the tooltip.
import { fmtDateTime } from "./dom.js";

const NS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}, text) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  if (text != null) n.textContent = text;
  return n;
};

export function usageTimeline(items, days, now = new Date()) {
  const end = now.getTime() + 2 * 3600e3;
  const start = now.getTime() - days * 86400e3;
  const W = 900, LEFT = 8, RIGHT = 8, LANE = 26, TOP = 22;
  const span = end - start;
  const x = (t) => LEFT + ((Math.min(Math.max(t, start), end) - start) / span) * (W - LEFT - RIGHT);

  const sessions = items
    .map((s) => ({ ...s, a: new Date(s.start_at).getTime(), b: new Date(s.end_at).getTime() }))
    .filter((s) => s.b >= start && s.a <= end)
    .sort((p, q) => p.a - q.a);

  const lanes = [];
  for (const s of sessions) {
    let lane = lanes.findIndex((last) => last <= s.a);
    if (lane === -1) { lane = lanes.length; lanes.push(0); }
    lanes[lane] = s.b;
    s.lane = lane;
  }
  const height = TOP + Math.max(lanes.length, 1) * LANE + 10;
  const root = svg("svg", { viewBox: `0 0 ${W} ${height}`, class: "timeline", role: "img",
    "aria-label": `Usage over the last ${days} days: ${sessions.length} sessions` });

  const tickEvery = days <= 2 ? 6 * 3600e3 : 86400e3;
  const first = Math.ceil(start / tickEvery) * tickEvery;
  for (let t = first; t < end; t += tickEvery) {
    root.append(svg("line", { x1: x(t), x2: x(t), y1: TOP - 6, y2: height, class: "tick" }));
    const d = new Date(t);
    root.append(svg("text", { x: x(t) + 3, y: 12, class: "tick-label" },
      days <= 2 ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                : d.toLocaleDateString([], { day: "numeric", month: "short" })));
  }
  const nowX = x(now.getTime());
  root.append(svg("line", { x1: nowX, x2: nowX, y1: TOP - 6, y2: height, class: "now" }));

  for (const s of sessions) {
    const x1 = x(s.a), w = Math.max(x(s.b) - x1, 6), y = TOP + s.lane * LANE;
    const g = svg("g", { class: `bar ${s.working ? "ok" : "bad"}` });
    const label = `${s.user_name}: ${fmtDateTime(s.start_at)} – ${fmtDateTime(s.end_at)}` +
      (s.working ? "" : ` — reported a fault: ${s.fault_note || ""}`);
    g.append(svg("title", {}, label));
    g.append(svg("rect", { x: x1, y, width: w, height: LANE - 6, rx: 5 }));
    if (w > 70) g.append(svg("text", { x: x1 + 6, y: y + 14, class: "bar-label" },
      `${s.working ? "" : "✗ "}${s.user_name}`));
    root.append(g);
  }
  return root;
}
