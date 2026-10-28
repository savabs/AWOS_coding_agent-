// /r/: show a shared AWOS Replay recipe from location.hash and hand it to the
// extension. The recipe is untrusted: every recipe string goes into the page
// with textContent, never innerHTML. Nothing is fetched; the fragment never
// leaves the browser.
//
// Page <-> extension handshake (window.postMessage, same window, same origin):
//   page -> ext  {type: "awos-replay-hello"}                 on load (are you there?)
//   ext  -> page {type: "awos-replay-present", version}      the content script is loaded
//   page -> ext  {type: "awos-import-recipe", recipe}        "Add to AWOS Replay" clicked
//   ext  -> page {type: "awos-import-result", ok, error?}    after the user confirms or cancels
// The extension may also set <html data-awos-replay="<version>">.
import { readRecipeLink, describeStep, checkLines, safeHttpUrl } from "./recipe-core.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

let current = null;
let present = Boolean(document.documentElement.getAttribute("data-awos-replay"));
let waiting = null;

function say(text, cls) {
  const s = $("r-status");
  s.className = "status" + (cls ? " " + cls : "");
  s.textContent = text;
}

function showInstall() {
  $("r-install").hidden = false;
}

function renderError(errors) {
  current = null;
  $("r-loading").hidden = true;
  $("r-view").hidden = true;
  $("r-title").textContent = "This recipe could not be opened";
  const list = $("r-errors");
  list.replaceChildren(...errors.slice(0, 8).map((e) => el("li", null, e)));
  $("r-error").hidden = false;
  document.title = "Recipe could not be opened · AWOS Replay";
}

function renderRecipe(r) {
  current = r;
  $("r-loading").hidden = true;
  $("r-error").hidden = true;
  $("r-title").textContent = r.name;
  document.title = r.name + " · AWOS Replay recipe";
  $("r-desc").textContent = r.description || "No description.";
  const u = safeHttpUrl(r.start_url);
  $("r-site").textContent = u ? u.host : r.start_url;
  $("r-url").textContent = r.start_url;
  $("r-count").textContent = r.steps.length + (r.steps.length === 1 ? " step" : " steps");
  $("r-made").textContent = r.created_with || "unknown";

  const pBody = $("r-params");
  pBody.replaceChildren(...r.params.map((p) => {
    const tr = el("tr");
    tr.append(el("td", null, p.label), el("td", "mono", p.name), el("td", null, p.example || "—"));
    return tr;
  }));
  $("r-params-wrap").hidden = r.params.length === 0;
  $("r-noparams").hidden = r.params.length !== 0;

  let danger = 0;
  $("r-steps").replaceChildren(...r.steps.map((s) => {
    const d = describeStep(s, r.params);
    const li = el("li");
    li.append(el("b", null, d.verb));
    if (d.target) li.append(" ", el("span", "tgt", d.target));
    if (d.value) {
      li.append(el("span", "val", d.ask ? " (" + d.value + ")" : " with " + d.value));
    }
    if (d.danger) {
      danger++;
      li.append(" ", el("span", "pill wait", "marked risky"));
    }
    return li;
  }));
  $("r-danger").hidden = danger === 0;

  const lines = checkLines(r.checks);
  $("r-checks").replaceChildren(...(lines.length ? lines : ["No checks: AWOS Replay will ask you to confirm the result."])
    .map((t) => el("li", null, t)));
  $("r-view").hidden = false;
  $("r-install").hidden = true;
  say("");
}

async function load() {
  $("r-loading").hidden = false;
  const res = await readRecipeLink(location.hash);
  if (res.ok) renderRecipe(res.recipe);
  else renderError(res.errors);
}

window.addEventListener("message", (ev) => {
  if (ev.source !== window || ev.origin !== location.origin) return;
  const d = ev.data;
  if (!d || typeof d !== "object") return;
  if (d.type === "awos-replay-present") present = true;
  if (d.type === "awos-import-result") {
    present = true;
    clearTimeout(waiting);
    if (d.ok) say("Added. Open AWOS Replay to run it.", "ok");
    else say(typeof d.error === "string" && d.error ? "Not added: " + d.error.slice(0, 200) : "Not added.", "err");
  }
});

$("r-add").addEventListener("click", () => {
  if (!current) return;
  window.postMessage({ type: "awos-import-recipe", recipe: current }, location.origin);
  if (present) {
    say("Confirm in the AWOS Replay window.");
    return;
  }
  say("Looking for AWOS Replay in this browser…");
  clearTimeout(waiting);
  waiting = setTimeout(() => {
    if (present) return;
    say("AWOS Replay is not installed in this browser yet.", "err");
    showInstall();
  }, 1200);
});

$("r-copy").addEventListener("click", () => {
  const done = () => say("Link copied.", "ok");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(location.href).then(done, () => say("Copy the address from the address bar.", "err"));
  } else say("Copy the address from the address bar.");
});

// In-page anchors (the skip link's #main) are not recipes: keep what is shown.
window.addEventListener("hashchange", () => {
  const frag = location.hash.slice(1);
  if (frag && /^[a-z][a-z-]*$/.test(frag) && document.getElementById(frag)) return;
  load();
});
window.postMessage({ type: "awos-replay-hello" }, location.origin);
load().catch(() => renderError(["Something went wrong while reading this recipe."]));
