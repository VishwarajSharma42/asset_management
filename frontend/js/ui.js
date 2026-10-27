import { h } from "./dom.js";

export function conditionBadge(asset) {
  if (asset.lifecycle === "retired") return h("span", { class: "badge neutral", text: "Retired" });
  return asset.condition === "faulty"
    ? h("span", { class: "badge bad", text: "⚠ Faulty" })
    : h("span", { class: "badge ok", text: "✓ Working" });
}

export function useBadge(n) {
  return n > 0 ? h("span", { class: "badge info", text: `${n} in use` }) : h("span", { class: "muted", text: "Idle" });
}

export function integrityBadge(ok) {
  return ok ? null : h("span", { class: "badge bad", title: "The signed record failed verification",
    text: "Integrity check failed" });
}

export function thumb(url, alt) {
  return url ? h("img", { class: "thumb", src: url, alt, loading: "lazy" })
             : h("span", { class: "thumb thumb-empty", "aria-hidden": "true" });
}

let toastHost;
export function toast(message, kind = "info") {
  if (!toastHost) { toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" }); document.body.append(toastHost); }
  const node = h("div", { class: `toast ${kind}`, text: message });
  toastHost.append(node);
  setTimeout(() => node.remove(), kind === "bad" ? 7000 : 3500);
}

/** A modal form. Resolves to {field: value} on submit, or null if cancelled. */
export function formDialog({ title, text, fields = [], submitLabel = "Save", danger = false }) {
  return new Promise((resolve) => {
    const inputs = {};
    const rows = fields.map((f) => {
      const control = f.type === "textarea"
        ? h("textarea", { id: `f-${f.name}`, maxlength: f.maxlength || 500, required: f.required !== false })
        : f.type === "select"
          ? h("select", { id: `f-${f.name}` }, f.options.map(([v, l]) => h("option", { value: v, text: l })))
          : h("input", { id: `f-${f.name}`, type: f.type || "text", required: f.required !== false,
              autocomplete: f.autocomplete || "off", maxlength: f.maxlength || 200 });
      if (f.value != null) control.value = f.value;
      inputs[f.name] = control;
      return h("div", { class: "field" }, h("label", { for: `f-${f.name}`, text: f.label }), control,
        f.hint ? h("p", { class: "hint", text: f.hint }) : null);
    });
    const dialog = h("dialog", { class: "modal" },
      h("form", { method: "dialog" },
        h("h2", { text: title }), text ? h("p", { class: "muted", text }) : null, rows,
        h("div", { class: "row end" },
          h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => dialog.close("cancel") }),
          h("button", { class: `btn ${danger ? "danger" : "primary"}`, type: "submit", text: submitLabel }))));
    dialog.addEventListener("close", () => {
      const ok = dialog.returnValue !== "cancel" && dialog.returnValue !== "";
      resolve(ok ? Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value])) : null);
      dialog.remove();
    });
    dialog.querySelector("form").addEventListener("submit", () => { dialog.returnValue = "ok"; });
    document.body.append(dialog);
    dialog.showModal();
    Object.values(inputs)[0]?.focus();
  });
}

export function errorBox(err) {
  return h("div", { class: "card pad notice bad", role: "alert" }, h("strong", { text: "Something went wrong. " }),
    err?.message || "Please try again.");
}

export function emptyState(text) {
  return h("div", { class: "empty muted", text });
}
