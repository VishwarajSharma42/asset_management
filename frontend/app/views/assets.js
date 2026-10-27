import { api, get, qs } from "../../js/api.js";
import { debounce, fmtDateTime, h, mount, relative } from "../../js/dom.js";
import { conditionBadge, emptyState, integrityBadge, thumb, toast, useBadge } from "../../js/ui.js";

const PAGE = 25;

export async function assetsView(root, { params }) {
  const state = {
    q: params.get("q") || "", room: params.get("room") || "", condition: params.get("condition") || "",
    in_use: params.get("in_use") === "1", lifecycle: params.get("lifecycle") || "active",
    sort: params.get("sort") || "name", offset: Number(params.get("offset") || 0),
  };
  const overview = await get("/overview");

  const q = h("input", { id: "q", type: "search", placeholder: "Name, room or product ID", value: state.q, maxlength: 60 });
  const room = h("select", { id: "room" }, h("option", { value: "", text: "All rooms" }),
    overview.rooms.map((r) => h("option", { value: r.room_no, text: r.room_no })));
  room.value = state.room;
  const cond = h("select", { id: "cond" }, [["", "Any status"], ["working", "Working"], ["faulty", "Faulty"]]
    .map(([v, l]) => h("option", { value: v, text: l })));
  cond.value = state.condition;
  const life = h("select", { id: "life" }, [["active", "In service"], ["retired", "Retired"]]
    .map(([v, l]) => h("option", { value: v, text: l })));
  life.value = state.lifecycle;
  const busy = h("input", { id: "busy", type: "checkbox" });
  busy.checked = state.in_use;

  const body = h("div");
  const push = () => {
    const query = qs({ q: state.q, room: state.room, condition: state.condition, in_use: state.in_use,
      lifecycle: state.lifecycle === "active" ? "" : state.lifecycle, sort: state.sort === "name" ? "" : state.sort,
      offset: state.offset || "" });
    history.replaceState(null, "", `#/assets${query}`);
  };

  async function load() {
    push();
    const data = await get(`/assets${qs({ ...state, limit: PAGE, offset: state.offset })}`);
    const th = (key, label) => {
      const active = state.sort.replace("-", "") === key;
      const desc = state.sort === `-${key}`;
      return h("th", { "aria-sort": active ? (desc ? "descending" : "ascending") : "none" },
        h("button", { type: "button", text: label + (active ? (desc ? " ▼" : " ▲") : ""), onclick: () => {
          state.sort = active && !desc ? `-${key}` : key; state.offset = 0; load();
        } }));
    };
    const rows = data.items.map((a) => h("tr", {},
      h("td", {}, h("div", { class: "asset-cell" }, thumb(a.image_url, ""),
        h("div", {}, h("a", { href: `#/assets/${a.id}`, text: a.name }), integrityBadge(a.integrity)))),
      h("td", { class: "mono", text: a.product_id ?? "—" }),
      h("td", { text: a.room_no }),
      h("td", {}, conditionBadge(a), a.open_faults ? h("span", { class: "muted", text: `  ${a.open_faults} open` }) : null),
      h("td", {}, useBadge(a.in_use_count)),
      h("td", { class: "muted", title: fmtDateTime(a.last_used_at), text: relative(a.last_used_at) })));
    const table = rows.length
      ? h("div", { class: "table-wrap" }, h("table", {},
          h("thead", {}, h("tr", {}, th("name", "Equipment"), h("th", { text: "Product ID" }), th("room", "Room"),
            th("condition", "Status"), h("th", { text: "Now" }), th("last_used", "Last used"))),
          h("tbody", {}, rows)))
      : emptyState("No equipment matches these filters.");
    const from = data.total ? state.offset + 1 : 0, to = Math.min(state.offset + PAGE, data.total);
    const prev = h("button", { class: "btn small", type: "button", text: "← Previous", disabled: state.offset === 0,
      onclick: () => { state.offset = Math.max(0, state.offset - PAGE); load(); } });
    const next = h("button", { class: "btn small", type: "button", text: "Next →", disabled: to >= data.total,
      onclick: () => { state.offset += PAGE; load(); } });
    mount(body, table, h("div", { class: "pager" }, h("span", { text: `${from}–${to} of ${data.total}` }), h("div", { class: "row" }, prev, next)));
  }

  const reset = () => { state.offset = 0; load().catch((e) => toast(e.message, "bad")); };
  q.addEventListener("input", debounce(() => { state.q = q.value.trim(); reset(); }));
  room.addEventListener("change", () => { state.room = room.value; reset(); });
  cond.addEventListener("change", () => { state.condition = cond.value; reset(); });
  life.addEventListener("change", () => { state.lifecycle = life.value; reset(); });
  busy.addEventListener("change", () => { state.in_use = busy.checked; reset(); });

  const exportCsv = h("button", { class: "btn", type: "button", text: "Export usage CSV", onclick: async () => {
    try {
      const res = await api("/export/usage.csv?days=30", { raw: true });
      const url = URL.createObjectURL(await res.blob());
      const a = h("a", { href: url, download: "usage-last-30-days.csv" });
      document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch (e) { toast(e.message, "bad"); }
  } });

  mount(root,
    h("div", { class: "page-head" }, h("h1", { text: "Equipment" }), exportCsv),
    h("div", { class: "card pad" },
      h("div", { class: "filters" },
        h("div", {}, h("label", { for: "q", text: "Search" }), q),
        h("div", {}, h("label", { for: "room", text: "Room" }), room),
        h("div", {}, h("label", { for: "cond", text: "Status" }), cond),
        h("div", {}, h("label", { for: "life", text: "Lifecycle" }), life),
        h("label", { class: "check", for: "busy" }, busy, "In use now")),
      body));
  await load();
}
