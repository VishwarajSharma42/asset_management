import { get, post, session } from "../../js/api.js";
import { fmtDateTime, h, mount, relative } from "../../js/dom.js";
import { emptyState, formDialog, toast } from "../../js/ui.js";

export async function faultsView(root, { params }) {
  let status = params.get("status") || "open";

  async function load() {
    const { items } = await get(`/faults?status=${status}`);
    const filter = h("select", { "aria-label": "Show" }, [["open", "Open"], ["resolved", "Resolved"], ["all", "All"]]
      .map(([v, l]) => h("option", { value: v, text: l })));
    filter.value = status;
    filter.addEventListener("change", () => { status = filter.value; load(); });

    const rows = items.map((f) => h("tr", {},
      h("td", {}, h("a", { href: `#/assets/${f.asset_id}`, text: f.asset_name })),
      h("td", { text: f.room_no }), h("td", { text: f.note }), h("td", { text: f.reported_by }),
      h("td", { title: fmtDateTime(f.reported_at), text: relative(f.reported_at) }),
      h("td", {}, f.resolved_at
        ? h("span", { class: "badge ok", title: f.resolution_note || "", text: "✓ Resolved" })
        : h("span", { class: "badge bad", text: "Open" })),
      h("td", {}, session.isAdmin() && !f.resolved_at ? h("button", { class: "btn small", type: "button", text: "Resolve", onclick: async () => {
        const v = await formDialog({ title: "Resolve fault", text: f.note, fields: [{ name: "note", label: "What was done?", type: "textarea" }], submitLabel: "Mark resolved" });
        if (!v) return;
        try { await post(`/faults/${f.id}/resolve`, { note: v.note }); toast("Resolved", "ok"); load(); }
        catch (e) { toast(e.message, "bad"); }
      } }) : null)));

    mount(root,
      h("div", { class: "page-head" }, h("h1", { text: "Fault reports" }), filter),
      items.length
        ? h("div", { class: "card table-wrap" }, h("table", {}, h("thead", {}, h("tr", {},
            ["Equipment", "Room", "Problem", "Reported by", "When", "Status", ""].map((t) => h("th", { text: t })))), h("tbody", {}, rows)))
        : h("div", { class: "card" }, emptyState(status === "open" ? "No open faults. 🎉" : "Nothing to show.")));
  }
  await load();
}
