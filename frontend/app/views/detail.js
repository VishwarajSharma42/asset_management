import { api, get, post, session } from "../../js/api.js";
import { fmtDateTime, fmtTime, h, mount, relative } from "../../js/dom.js";
import { conditionBadge, emptyState, formDialog, integrityBadge, toast, useBadge } from "../../js/ui.js";
import { usageTimeline } from "../../js/timeline.js";

export async function detailView(root, { match }) {
  const id = match[1];
  let tab = "usage";
  let days = 3;

  async function load() {
    const [asset, faults] = await Promise.all([get(`/assets/${id}`), get("/faults?status=all")]);
    const mine = faults.items.filter((f) => f.asset_id === id);
    const usage = await get(`/assets/${id}/usage?days=${days}`);
    draw(asset, mine, usage);
  }

  function actions(asset) {
    if (!session.isAdmin()) return null;
    const edit = h("button", { class: "btn", type: "button", text: "Edit", onclick: async () => {
      const v = await formDialog({ title: "Edit equipment", fields: [
        { name: "name", label: "Name", value: asset.name, maxlength: 100 },
        { name: "room_no", label: "Room no.", value: asset.room_no, maxlength: 20 }] });
      if (!v) return;
      try {
        await api(`/assets/${id}`, { method: "PATCH", body: v, headers: { "If-Match": String(asset.profile_version) } });
        toast("Saved", "ok"); load();
      } catch (e) { toast(e.code === "version_conflict" ? "Someone else edited this. Reloading." : e.message, "bad"); load(); }
    } });
    const retiring = asset.lifecycle === "active";
    const life = h("button", { class: "btn danger", type: "button", text: retiring ? "Retire" : "Restore", onclick: async () => {
      const v = await formDialog({ title: retiring ? "Retire this equipment?" : "Restore this equipment?",
        text: retiring ? "Retired equipment can no longer be used or logged." : "It will accept usage logs again.",
        fields: [{ name: "reason", label: "Reason", type: "textarea" }], submitLabel: retiring ? "Retire" : "Restore", danger: retiring });
      if (!v) return;
      try { await post(`/assets/${id}/lifecycle`, { lifecycle: retiring ? "retired" : "active", reason: v.reason }); toast("Updated", "ok"); load(); }
      catch (e) { toast(e.message, "bad"); }
    } });
    return h("div", { class: "row" }, edit, life);
  }

  function resolveButton(f) {
    if (!session.isAdmin() || f.resolved_at) return null;
    return h("button", { class: "btn small", type: "button", text: "Resolve", onclick: async () => {
      const v = await formDialog({ title: "Resolve fault", text: f.note, fields: [{ name: "note", label: "What was done?", type: "textarea" }], submitLabel: "Mark resolved" });
      if (!v) return;
      try { await post(`/faults/${f.id}/resolve`, { note: v.note }); toast("Resolved", "ok"); load(); }
      catch (e) { toast(e.message, "bad"); }
    } });
  }

  function usageTab(usage) {
    const rows = usage.items.map((s) => h("tr", {},
      h("td", { text: s.user_name }),
      h("td", { text: `${fmtDateTime(s.start_at)} → ${fmtTime(s.end_at)}` }),
      h("td", {}, s.working ? h("span", { class: "badge ok", text: "✓ Working" })
                            : h("span", { class: "badge bad", title: s.fault_note || "", text: "✗ Fault" })),
      h("td", {}, s.active ? h("span", { class: "badge info", text: "In use" }) : null,
        s.overlaps ? h("span", { class: "muted", text: ` shared with ${s.overlaps}` }) : null)));
    const range = h("select", { "aria-label": "Time range" }, [[1, "Last 24 hours"], [3, "Last 3 days"], [7, "Last 7 days"], [14, "Last 14 days"], [30, "Last 30 days"]]
      .map(([v, l]) => h("option", { value: v, text: l })));
    range.value = String(days);
    range.addEventListener("change", () => { days = Number(range.value); load(); });
    return h("div", { class: "stack" },
      h("div", { class: "row" }, h("h2", { text: "Usage timeline" }), range),
      usage.items.length ? h("div", { class: "card pad" }, usageTimeline(usage.items, days)) : emptyState("No usage logged in this period."),
      usage.items.length ? h("div", { class: "card table-wrap" }, h("table", {}, h("thead", {}, h("tr", {},
        h("th", { text: "Person" }), h("th", { text: "When" }), h("th", { text: "Result" }), h("th", { text: "" }))), h("tbody", {}, rows))) : null);
  }

  function faultsTab(mine) {
    if (!mine.length) return emptyState("No fault reports for this equipment.");
    return h("div", { class: "card table-wrap" }, h("table", {}, h("thead", {}, h("tr", {},
      h("th", { text: "Reported" }), h("th", { text: "Problem" }), h("th", { text: "By" }), h("th", { text: "Status" }), h("th", { text: "" }))),
      h("tbody", {}, mine.map((f) => h("tr", {},
        h("td", { text: fmtDateTime(f.reported_at) }), h("td", { text: f.note }), h("td", { text: f.reported_by }),
        h("td", {}, f.resolved_at ? h("span", { class: "badge ok", title: f.resolution_note || "", text: `Resolved ${relative(f.resolved_at)}` })
                                  : h("span", { class: "badge bad", text: "Open" })),
        h("td", {}, resolveButton(f)))))));
  }

  function detailsTab(asset) {
    const rows = [["Product ID", asset.product_id ?? "—"], ["Room", asset.room_no], ["Registered", fmtDateTime(asset.created_at)],
      ["Last used", relative(asset.last_used_at)]];
    if (asset.tag_uid) rows.push(["Tag UID", asset.tag_uid]);
    if (asset.lock_requested != null) rows.push(["Tag lock requested", asset.lock_requested ? "Yes" : "No"]);
    const copy = asset.tag_link ? h("button", { class: "btn small", type: "button", text: "Copy tag link", onclick: async () => {
      try { await navigator.clipboard.writeText(asset.tag_link); toast("Link copied", "ok"); } catch { toast("Copy failed", "bad"); }
    } }) : null;
    return h("div", { class: "card pad" }, h("dl", { class: "kv" }, rows.map(([k, v]) => [h("dt", { text: k }), h("dd", { class: k === "Tag UID" ? "mono" : "", text: v })])), copy);
  }

  function draw(asset, mine, usage) {
    const openFaults = mine.filter((f) => !f.resolved_at).length;
    const tabs = [["usage", "Usage"], ["faults", `Faults${openFaults ? ` (${openFaults})` : ""}`], ["details", "Details"]];
    const panel = h("div");
    const show = () => mount(panel, tab === "usage" ? usageTab(usage) : tab === "faults" ? faultsTab(mine) : detailsTab(asset));
    const tabBar = h("div", { class: "tabs", role: "tablist" }, tabs.map(([key, label]) => h("button", {
      type: "button", role: "tab", "aria-selected": String(tab === key), text: label,
      onclick: (e) => { tab = key; tabBar.querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", String(b === e.currentTarget))); show(); } })));
    show();
    mount(root,
      h("p", {}, h("a", { href: "#/assets", text: "← All equipment" })),
      h("div", { class: "card pad detail-head" },
        asset.image_url ? h("img", { src: asset.image_url, alt: `Photo of ${asset.name}` }) : h("div", { class: "photo-empty", text: "No photo" }),
        h("div", {},
          h("div", { class: "row" }, conditionBadge(asset), useBadge(asset.in_use_count), integrityBadge(asset.integrity)),
          h("h1", { text: asset.name }),
          h("p", { class: "muted", text: `Room ${asset.room_no}${asset.product_id ? ` · ${asset.product_id}` : ""}` }),
          actions(asset))),
      tabBar, panel);
  }

  mount(root, h("p", { class: "muted", text: "Loading…" }));
  await load();
}
