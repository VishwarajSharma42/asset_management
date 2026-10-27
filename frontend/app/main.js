import { post, session } from "../js/api.js";
import { h, mount } from "../js/dom.js";
import { errorBox, toast } from "../js/ui.js";
import { loginView } from "./views/login.js";
import { overviewView } from "./views/overview.js";
import { assetsView } from "./views/assets.js";
import { detailView } from "./views/detail.js";
import { faultsView } from "./views/faults.js";
import { usersView } from "./views/users.js";
import { auditView } from "./views/audit.js";

const app = document.getElementById("app");
const ROUTES = [
  { re: /^\/?$/, view: overviewView, nav: "overview" },
  { re: /^\/assets$/, view: assetsView, nav: "assets" },
  { re: /^\/assets\/([0-9a-f-]{36})$/, view: detailView, nav: "assets" },
  { re: /^\/faults$/, view: faultsView, nav: "faults" },
  { re: /^\/users$/, view: usersView, nav: "users", admin: true },
  { re: /^\/audit$/, view: auditView, nav: "audit", admin: true },
];
let sessionMessage = "";

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  return { path, params: new URLSearchParams(query) };
}

function navLink(href, key, label, current) {
  return h("a", { href, "aria-current": current === key ? "page" : false, text: label });
}

function shell(current) {
  const user = session.user;
  const links = [
    navLink("#/", "overview", "Overview", current),
    navLink("#/assets", "assets", "Equipment", current),
    navLink("#/faults", "faults", "Faults", current),
    session.isAdmin() ? navLink("#/users", "users", "Users", current) : null,
    session.isAdmin() ? navLink("#/audit", "audit", "Audit log", current) : null,
  ];
  const view = h("main", { class: "page", id: "view" });
  const out = h("button", { class: "btn small", type: "button", text: "Sign out",
    onclick: () => { session.clear(); location.hash = "#/"; } });
  const top = h("header", { class: "bar" }, h("div", { class: "bar-in" },
    h("div", { class: "brand" }, h("span", { class: "dot" }), "DSO Asset Register"),
    h("nav", { "aria-label": "Main" }, links),
    h("div", { class: "who" }, h("span", { text: `${user.display_name} · ${user.role}` }), out)));
  mount(app, h("div", { class: "shell" }, top, view));
  return view;
}

async function render() {
  if (!session.token) {
    mount(app, loginView(async (creds) => {
      const data = await post("/auth/login", creds);
      session.set(data.token, { display_name: data.display_name, username: data.username, role: data.role });
      sessionMessage = "";
      render();
    }, sessionMessage));
    return;
  }
  const { path, params } = parseHash();
  const route = ROUTES.find((r) => r.re.test(path));
  const view = shell(route?.nav);
  if (!route || (route.admin && !session.isAdmin())) {
    mount(view, h("div", { class: "card pad" }, h("h1", { text: "Not found" }), h("a", { href: "#/", text: "Go to overview" })));
    return;
  }
  const match = path.match(route.re);
  try { await route.view(view, { params, match }); }
  catch (err) { mount(view, errorBox(err)); }
}

session.onChange(() => {
  if (!session.token) { sessionMessage = sessionMessage || "Signed out."; render(); }
});
window.addEventListener("hashchange", render);
window.addEventListener("unhandledrejection", (e) => { toast(e.reason?.message || "Unexpected error", "bad"); });
sessionMessage = "";
render();
