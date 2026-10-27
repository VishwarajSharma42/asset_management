import { get, post, session } from "../../js/api.js";
import { fmtDateTime, h, mount } from "../../js/dom.js";
import { formDialog, toast } from "../../js/ui.js";

export async function usersView(root) {
  async function load() {
    const { items } = await get("/users");
    const me = session.user.username;
    const add = h("button", { class: "btn primary", type: "button", text: "Add staff account", onclick: async () => {
      const v = await formDialog({ title: "New staff account", submitLabel: "Create", fields: [
        { name: "username", label: "Username", maxlength: 32, hint: "3–32 characters: a-z, 0-9, . _ -" },
        { name: "display_name", label: "Full name", maxlength: 80 },
        { name: "role", label: "Role", type: "select", options: [["viewer", "Viewer — read only"], ["admin", "Admin — can edit and manage"]] },
        { name: "password", label: "Temporary password", type: "password", autocomplete: "new-password", hint: "At least 12 characters. Ask them to change it after signing in." }] });
      if (!v) return;
      try { await post("/users", v); toast("Account created", "ok"); load(); } catch (e) { toast(e.message, "bad"); }
    } });
    const rows = items.map((u) => h("tr", {},
      h("td", {}, h("strong", { text: u.username }), u.username === me ? h("span", { class: "muted", text: " (you)" }) : null),
      h("td", { text: u.display_name }),
      h("td", {}, h("span", { class: `badge ${u.role === "admin" ? "info" : "neutral"}`, text: u.role })),
      h("td", {}, u.is_active ? h("span", { class: "badge ok", text: "Active" }) : h("span", { class: "badge bad", text: "Disabled" }),
        u.locked ? h("span", { class: "badge warn", text: "Locked" }) : null),
      h("td", { class: "muted", text: fmtDateTime(u.last_login_at) }),
      h("td", {}, h("div", { class: "row" },
        u.username === me ? null : h("button", { class: "btn small", type: "button", text: u.is_active ? "Disable" : "Enable", onclick: async () => {
          try { await post(`/users/${u.id}/active`, { active: !u.is_active }); toast("Updated", "ok"); load(); } catch (e) { toast(e.message, "bad"); }
        } }),
        h("button", { class: "btn small", type: "button", text: "Reset password", onclick: async () => {
          const v = await formDialog({ title: `Reset password for ${u.username}`, text: "They will be signed out everywhere.",
            fields: [{ name: "password", label: "New password", type: "password", autocomplete: "new-password" }], submitLabel: "Reset" });
          if (!v) return;
          try { await post(`/users/${u.id}/password`, { password: v.password }); toast("Password reset", "ok"); load(); } catch (e) { toast(e.message, "bad"); }
        } })))));
    mount(root,
      h("div", { class: "page-head" }, h("h1", { text: "Staff accounts" }), add),
      h("div", { class: "card table-wrap" }, h("table", {}, h("thead", {}, h("tr", {},
        ["Username", "Name", "Role", "State", "Last sign-in", ""].map((t) => h("th", { text: t })))), h("tbody", {}, rows))));
  }
  await load();
}
