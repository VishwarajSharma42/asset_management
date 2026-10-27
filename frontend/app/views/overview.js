import { get } from "../../js/api.js";
import { h, mount, relative } from "../../js/dom.js";
import { emptyState } from "../../js/ui.js";

function tile(href, n, label, kind = "") {
  return h("a", { class: `tile ${kind}`, href }, h("div", { class: "n", text: n }), h("div", { class: "l", text: label }));
}

export async function overviewView(root) {
  mount(root, h("p", { class: "muted", text: "Loading…" }));
  const [o, faults] = await Promise.all([get("/overview"), get("/faults?status=open")]);

  const rooms = o.rooms.length
    ? h("ul", { class: "stack", style: null }, o.rooms.map((r) => h("li", { class: "row" },
        h("a", { href: `#/assets?room=${encodeURIComponent(r.room_no)}`, text: `Room ${r.room_no}` }),
        h("span", { class: "muted", text: `${r.count} item${r.count === 1 ? "" : "s"}` }))))
    : emptyState("No equipment registered yet.");

  const recent = faults.items.slice(0, 6);
  const faultList = recent.length
    ? h("ul", { class: "stack" }, recent.map((f) => h("li", {},
        h("a", { href: `#/assets/${f.asset_id}`, text: f.asset_name }),
        h("span", { class: "muted", text: ` · Room ${f.room_no} · ${relative(f.reported_at)}` }),
        h("div", { text: f.note }))))
    : emptyState("No open faults.");

  mount(root,
    h("div", { class: "page-head" }, h("h1", { text: "Overview" })),
    h("div", { class: "tiles" },
      tile("#/assets", o.total_assets, "Equipment registered"),
      tile("#/assets?in_use=1", o.in_use_now, "In use right now", "info"),
      tile("#/assets?condition=faulty", o.faulty, "Faulty", o.faulty ? "bad" : "ok"),
      tile("#/faults", o.open_faults, "Open fault reports", o.open_faults ? "bad" : ""),
      h("div", { class: "tile" }, h("div", { class: "n", text: o.sessions_today }), h("div", { class: "l", text: "Sessions today" }))),
    h("div", { class: "cols" },
      h("section", { class: "card pad" }, h("h2", { text: "Open faults" }), faultList),
      h("section", { class: "card pad" }, h("h2", { text: "By room" }), rooms)));
}
