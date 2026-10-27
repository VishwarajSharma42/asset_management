import { clear, h, localInputValue, mount, safeStorage } from "./dom.js";

const root = document.getElementById("root");
const params = new URLSearchParams(location.search);
const NAME_KEY = "assetreg.user_name";

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    credentials: "omit", referrerPolicy: "no-referrer",
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error page */ }
  return { ok: res.ok, status: res.status, data };
}

function notice(kind, title, text) {
  return h("div", { class: `card pad notice ${kind}` }, h("h1", { text: title }), h("p", { text }));
}

const MESSAGES = {
  unregistered: ["warn", "Tag not recognised", "This tag is not in the register. Please tell the asset in-charge so it can be checked."],
  tag_mismatch: ["warn", "Tag needs a check", "The information on this tag does not match the register. Please tell the asset in-charge."],
  integrity_error: ["bad", "Record needs attention", "This record could not be verified. Please tell the asset in-charge."],
  retired: ["info", "Asset retired", "This asset has been taken out of service."],
};

function passport(asset) {
  const faulty = asset.condition === "faulty";
  return h("section", { class: "card passport" },
    asset.image_url
      ? h("img", { class: "photo", src: asset.image_url, alt: `Photo of ${asset.name}`, loading: "eager" })
      : h("div", { class: "photo-empty", "aria-hidden": "true" }, "No photo"),
    h("div", { class: "pad" },
      h("div", { class: "row" },
        h("span", { class: `badge ${faulty ? "bad" : "ok"}`, text: faulty ? "⚠ Reported faulty" : "✓ Working" }),
        asset.in_use_count > 0 ? h("span", { class: "badge info", text: `${asset.in_use_count} in use now` }) : null),
      h("h1", { text: asset.name }),
      h("dl", { class: "facts" },
        h("dt", { text: "Room" }), h("dd", { text: asset.room_no }))));
}

// Asks the phone where it is. Resolves to {location} or {location_error}; never rejects.
// Must be called from the submit handler (a user gesture) so the browser shows its prompt.
function getLocation() {
  return new Promise((resolve) => {
    if (!("geolocation" in navigator) || !window.isSecureContext) { resolve({ location_error: "unavailable" }); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ location: { lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy } }),
      (err) => resolve({ location_error: err.code === 1 ? "denied" : err.code === 3 ? "timeout" : "unavailable" }),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
  });
}

function usageForm(ticket, locationRequired, onDone) {
  const store = safeStorage();
  const now = new Date();
  const later = new Date(now.getTime() + 60 * 60 * 1000);

  const name = h("input", { id: "user_name", type: "text", autocomplete: "name", maxlength: 80, required: true,
    value: store?.getItem(NAME_KEY) || "" });
  const start = h("input", { id: "start_at", type: "datetime-local", required: true, value: localInputValue(now) });
  const end = h("input", { id: "end_at", type: "datetime-local", required: true, value: localInputValue(later) });
  const yes = h("input", { type: "radio", name: "working", id: "w-yes", value: "yes", required: true });
  const no = h("input", { type: "radio", name: "working", id: "w-no", value: "no" });
  const note = h("textarea", { id: "fault_note", maxlength: 500, placeholder: "e.g. Channel 2 shows no signal" });
  const noteField = h("div", { class: "field hidden" },
    h("label", { for: "fault_note", text: "What is wrong?" }), note,
    h("p", { class: "hint", text: "Staff will see this on their dashboard." }));
  const error = h("p", { class: "error-text", role: "alert" });
  const submit = h("button", { class: "btn primary", type: "submit", text: "Submit" });

  const sync = () => { noteField.classList.toggle("hidden", !no.checked); note.required = no.checked; };
  yes.addEventListener("change", sync); no.addEventListener("change", sync);

  const form = h("form", { class: "card pad", novalidate: false },
    h("h2", { text: "Log your use" }),
    h("p", { class: "muted", text: "Tell us who used this asset and whether it worked." }),
    h("div", { class: "field" }, h("label", { for: "user_name", text: "Your name" }), name),
    h("div", { class: "grid-2" },
      h("div", { class: "field" }, h("label", { for: "start_at", text: "Start time" }), start),
      h("div", { class: "field" }, h("label", { for: "end_at", text: "End time" }), end)),
    h("fieldset", { class: "field", style: null },
      h("label", { text: "Is the asset working properly?" }),
      h("div", { class: "choice" },
        yes, h("label", { for: "w-yes", class: "yes", text: "✓ Yes" }),
        no, h("label", { for: "w-no", class: "no", text: "✗ No" }))),
    noteField,
    h("p", { class: "hint", text: locationRequired
      ? "Your phone's location is required and will be recorded with this entry. Your browser will ask permission."
      : "Your phone's location will be recorded with this entry if you allow it." }),
    error, submit);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    const startDate = new Date(start.value), endDate = new Date(end.value);
    if (Number.isNaN(startDate) || Number.isNaN(endDate) || endDate < startDate) {
      error.textContent = "The end time must be after the start time.";
      return;
    }
    submit.disabled = true; submit.textContent = "Getting location…";
    const place = await getLocation();
    if (locationRequired && !place.location) {
      error.textContent = place.location_error === "denied"
        ? "Location is required. Allow location access for this site in your browser, then press Submit again."
        : "Could not get your location. Check that location is switched on, then press Submit again.";
      submit.disabled = false; submit.textContent = "Submit";
      return;
    }
    submit.textContent = "Sending…";
    const { ok, data } = await post("/api/v1/usage", {
      ticket, user_name: name.value, start_at: startDate.toISOString(), end_at: endDate.toISOString(),
      working: yes.checked, fault_note: no.checked ? note.value : null, ...place,
    });
    if (ok) {
      try { store?.setItem(NAME_KEY, name.value.trim()); } catch { /* private mode */ }
      onDone(data);
    } else {
      error.textContent = data?.error?.message || "Could not send. Please try again.";
      submit.disabled = false; submit.textContent = "Submit";
    }
  });
  return form;
}

async function main() {
  const t = params.get("t"), d = params.get("d");
  if (!t || !d) {
    mount(root, notice("warn", "No tag detected", "Hold your phone against the tag on the asset to open this page."));
    return;
  }
  let res;
  try { res = await post("/api/v1/scan", { t, d }); }
  catch { mount(root, notice("bad", "Can't reach the register", "Check that you are on the campus network and try again.")); return; }

  if (res.status === 429) { mount(root, notice("warn", "Too many requests", "Please wait a minute and tap the tag again.")); return; }
  if (res.status === 403) { mount(root, notice("warn", "Campus network only", res.data?.error?.message || "Please connect to the campus network.")); return; }
  if (!res.ok || !res.data) { mount(root, notice("bad", "Something went wrong", "Please try again in a moment.")); return; }

  const verdict = res.data.verdict;
  if (verdict !== "ok") {
    const [kind, title, text] = MESSAGES[verdict] || MESSAGES.unregistered;
    mount(root, notice(kind, title, text));
    return;
  }

  const slot = h("div");
  const passportSlot = h("div");
  const drawPassport = () => mount(passportSlot, passport(res.data.asset));
  const show = () => mount(slot, usageForm(res.data.ticket, res.data.location_required === true, (out) => {
    if (out.fault_flagged) { res.data.asset.condition = "faulty"; drawPassport(); }
    mount(slot, h("div", { class: "card pad notice ok" },
      h("h2", { text: "Thank you — recorded" }),
      h("p", { text: out.fault_flagged ? "The fault has been flagged for the staff." : "Your use has been logged." }),
      h("p", { class: "muted", text: "To log another use, tap the tag again." })));
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  }));
  show();
  drawPassport();
  mount(root, passportSlot, slot);
}

main();
