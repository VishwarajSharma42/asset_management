import { get } from "../../js/api.js";
import { h, mount } from "../../js/dom.js";
import { toast } from "../../js/ui.js";

export async function auditView(root) {
  const status = h("div", { class: "muted", text: "Chain not checked yet." });
  const verify = h("button", { class: "btn", type: "button", text: "Verify hash chain", onclick: async () => {
    verify.disabled = true;
    try {
      const r = await get("/audit/verify");
      mount(status, r.ok
        ? h("span", { class: "badge ok", text: `✓ Chain intact · ${r.checked} entries` })
        : h("span", { class: "badge bad", text: `✗ Broken at entry #${r.broken_at_seq}` }));
      if (r.head) mount(status, status.firstChild, h("div", { class: "mono muted", text: `head ${r.head.slice(0, 24)}…` }));
    } catch (e) { toast(e.message, "bad"); }
    verify.disabled = false;
  } });

  const { items } = await get("/audit?limit=200");
  const rows = items.map((e) => h("tr", {},
    h("td", { class: "mono", text: e.seq }),
    h("td", { text: new Date(e.ts).toLocaleString() }),
    h("td", { text: e.actor }), h("td", {}, h("span", { class: "badge neutral", text: e.event })),
    h("td", { class: "audit-detail", text: JSON.stringify(e.detail) })));
  mount(root,
    h("div", { class: "page-head" }, h("h1", { text: "Audit log" }), h("div", { class: "row" }, status, verify)),
    h("p", { class: "muted", text: "Every change is chained to the one before it. Editing or deleting a past entry breaks the chain, and verification shows where." }),
    h("div", { class: "card table-wrap" }, h("table", {}, h("thead", {}, h("tr", {},
      ["#", "Time", "Actor", "Event", "Detail"].map((t) => h("th", { text: t })))), h("tbody", {}, rows))));
}
