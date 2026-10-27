import { h } from "../../js/dom.js";

export function loginView(onSubmit, message) {
  const user = h("input", { id: "u", type: "text", autocomplete: "username", required: true, maxlength: 32, autocapitalize: "none" });
  const pass = h("input", { id: "p", type: "password", autocomplete: "current-password", required: true, maxlength: 256 });
  const error = h("p", { class: "error-text", role: "alert", text: message || "" });
  const btn = h("button", { class: "btn primary", type: "submit", text: "Sign in" });
  const form = h("form", { class: "card pad" },
    h("h1", { text: "DSO Asset Register" }),
    h("p", { class: "muted", text: "Staff sign-in" }),
    h("div", { class: "field" }, h("label", { for: "u", text: "Username" }), user),
    h("div", { class: "field" }, h("label", { for: "p", text: "Password" }), pass),
    error, btn);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = ""; btn.disabled = true; btn.textContent = "Signing in…";
    try { await onSubmit({ username: user.value, password: pass.value }); }
    catch (err) {
      error.textContent = err.message;
      btn.disabled = false; btn.textContent = "Sign in"; pass.value = ""; pass.focus();
    }
  });
  queueMicrotask(() => user.focus());
  return h("div", { class: "login-wrap" }, h("div", { class: "login" }, form));
}
