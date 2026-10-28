// AWOS Replay recipe links: decode, validate and describe a recipe.
// A recipe link is /r/#<base64url(deflate-raw(JSON))>. The fragment never
// reaches the server; everything here runs in the browser (and in Node >= 20
// for tests). Recipes are untrusted input: this module only returns plain
// strings and never touches the DOM.
//
// Contract v1:
//   {awos_recipe: 1, name, description, start_url,
//    params: [{name, label, example, kind: "text"}],
//    steps: [objects, rendered best-effort],
//    checks: {goal, expect_text: [str], expect_url: str|null,
//             expect_download: {name_regex: str}|null},
//    created_with}

export const RECIPE_VERSION = 1;
export const MAX_BYTES = 64 * 1024;              // decoded JSON
const MAX_FRAGMENT = Math.ceil(MAX_BYTES * 4 / 3) + 16; // a fragment can't usefully be longer than this
const MAX_TEXT = 2000;
const MAX_STEPS = 200;
const MAX_PARAMS = 30;
const PARAM_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,39}$/;
const PARAM_REF = /\{\{\s*([A-Za-z_][A-Za-z0-9_]{0,39})\s*\}\}/g;

export class RecipeError extends Error {}

// ── encoding ────────────────────────────────────────────────────────────────

export function base64urlEncode(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function base64urlDecode(s) {
  if (!/^[A-Za-z0-9_-]*={0,2}$/.test(s)) throw new RecipeError("The link is damaged: it contains characters a recipe link never has.");
  let b64 = s.replace(/=+$/, "").replace(/-/g, "+").replace(/_/g, "/");
  if (b64.length % 4 === 1) throw new RecipeError("The link is damaged: it looks cut off.");
  while (b64.length % 4) b64 += "=";
  let bin;
  try { bin = atob(b64); } catch { throw new RecipeError("The link is damaged: it is not valid base64."); }
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function pipeBytes(bytes, stream, limit) {
  const writer = stream.writable.getWriter();
  writer.write(bytes).catch(() => {});
  writer.close().catch(() => {});
  const reader = stream.readable.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (limit && total > limit) {
      reader.cancel().catch(() => {});
      throw new RecipeError(`This recipe is larger than ${MAX_BYTES / 1024} KB, so it was not opened.`);
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  return out;
}

export async function encodeRecipe(recipe) {
  const raw = new TextEncoder().encode(JSON.stringify(recipe));
  const packed = await pipeBytes(raw, new CompressionStream("deflate-raw"), 0);
  return base64urlEncode(packed);
}

// "#abc", "abc" or a full URL -> recipe object (not yet validated).
export async function decodeFragment(input) {
  let frag = String(input ?? "");
  const hashAt = frag.indexOf("#");
  if (hashAt >= 0) frag = frag.slice(hashAt + 1);
  frag = frag.trim();
  if (!frag) throw new RecipeError("This link has no recipe in it. Recipe links look like /r/#… with a long code after the #.");
  if (frag.length > MAX_FRAGMENT) throw new RecipeError(`This recipe is larger than ${MAX_BYTES / 1024} KB, so it was not opened.`);
  const packed = base64urlDecode(frag);
  let raw;
  try {
    raw = await pipeBytes(packed, new DecompressionStream("deflate-raw"), MAX_BYTES);
  } catch (e) {
    if (e instanceof RecipeError) throw e;
    throw new RecipeError("The link is damaged: the recipe inside could not be unpacked.");
  }
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(raw); } catch {
    throw new RecipeError("The link is damaged: the recipe is not valid text.");
  }
  try { return JSON.parse(text); } catch {
    throw new RecipeError("The link is damaged: the recipe is not valid JSON.");
  }
}

// ── validation ──────────────────────────────────────────────────────────────

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

function str(v, field, errors, { required = false, max = MAX_TEXT } = {}) {
  if (v == null || v === "") {
    if (required) errors.push(`"${field}" is missing.`);
    return "";
  }
  if (typeof v !== "string") { errors.push(`"${field}" must be text.`); return ""; }
  if (v.length > max) { errors.push(`"${field}" is longer than ${max} characters.`); return ""; }
  return v;
}

export function safeHttpUrl(v) {
  if (typeof v !== "string" || !v) return null;
  try {
    const u = new URL(v);
    return u.protocol === "https:" || u.protocol === "http:" ? u : null;
  } catch { return null; }
}

// -> {ok: true, recipe} (normalised copy) or {ok: false, errors: [str]}
export function validateRecipe(r) {
  const errors = [];
  if (!isObj(r)) return { ok: false, errors: ["This is not an AWOS Replay recipe."] };
  if (r.awos_recipe !== RECIPE_VERSION) {
    const v = r.awos_recipe;
    return { ok: false, errors: [v === undefined
      ? "This is not an AWOS Replay recipe (no awos_recipe version)."
      : `This recipe uses format version ${JSON.stringify(v).slice(0, 20)}; this page reads version ${RECIPE_VERSION}. Update AWOS Replay or ask the sender for a new link.`] };
  }
  const out = { awos_recipe: RECIPE_VERSION };
  out.name = str(r.name, "name", errors, { required: true, max: 120 });
  out.description = str(r.description, "description", errors);
  out.start_url = str(r.start_url, "start_url", errors, { required: true });
  if (out.start_url && !safeHttpUrl(out.start_url)) errors.push(`"start_url" must be an http or https address.`);
  out.created_with = str(r.created_with, "created_with", errors, { max: 120 });

  out.params = [];
  if (r.params != null && !Array.isArray(r.params)) errors.push(`"params" must be a list.`);
  else if ((r.params || []).length > MAX_PARAMS) errors.push(`More than ${MAX_PARAMS} params.`);
  else {
    const seen = new Set();
    (r.params || []).forEach((p, i) => {
      if (!isObj(p)) { errors.push(`params[${i}] must be an object.`); return; }
      const name = str(p.name, `params[${i}].name`, errors, { required: true, max: 40 });
      if (name && !PARAM_NAME.test(name)) errors.push(`params[${i}].name must be letters, digits or _.`);
      if (name && seen.has(name)) errors.push(`Param "${name}" is listed twice.`);
      seen.add(name);
      const kind = p.kind == null ? "text" : p.kind;
      if (kind !== "text") errors.push(`params[${i}].kind must be "text".`);
      out.params.push({ name, label: str(p.label, `params[${i}].label`, errors, { max: 120 }) || name,
        example: str(p.example, `params[${i}].example`, errors, { max: 200 }), kind: "text" });
    });
  }

  out.steps = [];
  if (!Array.isArray(r.steps) || r.steps.length === 0) errors.push(`"steps" must be a non-empty list.`);
  else if (r.steps.length > MAX_STEPS) errors.push(`More than ${MAX_STEPS} steps.`);
  else r.steps.forEach((s, i) => {
    if (!isObj(s)) errors.push(`steps[${i}] must be an object.`);
    else out.steps.push(s);
  });

  const c = r.checks == null ? {} : r.checks;
  out.checks = { goal: "", expect_text: [], expect_url: null, expect_download: null };
  if (!isObj(c)) errors.push(`"checks" must be an object.`);
  else {
    out.checks.goal = str(c.goal, "checks.goal", errors);
    if (c.expect_text != null) {
      if (!Array.isArray(c.expect_text) || c.expect_text.some((t) => typeof t !== "string" || t.length > 500)) {
        errors.push(`"checks.expect_text" must be a list of short texts.`);
      } else out.checks.expect_text = c.expect_text.slice(0, 20);
    }
    if (c.expect_url != null) out.checks.expect_url = str(c.expect_url, "checks.expect_url", errors) || null;
    if (c.expect_download != null) {
      if (!isObj(c.expect_download) || typeof c.expect_download.name_regex !== "string") {
        errors.push(`"checks.expect_download" must be {"name_regex": "..."} or null.`);
      } else {
        const rx = str(c.expect_download.name_regex, "checks.expect_download.name_regex", errors, { max: 300 });
        out.checks.expect_download = { name_regex: rx };
      }
    }
  }

  // Every {{param}} used by a step must be declared.
  const declared = new Set(out.params.map((p) => p.name));
  for (const s of out.steps) {
    for (const ref of paramRefs(stepValue(s))) {
      if (!declared.has(ref)) { errors.push(`A step uses {{${ref}}} but no param is called "${ref}".`); break; }
    }
  }
  return errors.length ? { ok: false, errors } : { ok: true, recipe: out };
}

export async function readRecipeLink(input) {
  try {
    return validateRecipe(await decodeFragment(input));
  } catch (e) {
    if (e instanceof RecipeError) return { ok: false, errors: [e.message] };
    throw e;
  }
}

// ── plain-language rendering (strings only) ─────────────────────────────────

export function paramRefs(s) {
  if (typeof s !== "string") return [];
  return [...s.matchAll(PARAM_REF)].map((m) => m[1]);
}

const firstText = (...vals) => {
  for (const v of vals) if (typeof v === "string" && v.trim()) return v.trim().replace(/\s+/g, " ").slice(0, 120);
  return "";
};

function stepValue(s) {
  for (const k of ["value", "text_value", "optionText"]) if (typeof s[k] === "string") return s[k];
  return "";
}

function stepTarget(s) {
  const loc = isObj(s.loc) ? s.loc : {};
  const tgt = isObj(s.target) ? s.target : {};
  return firstText(s.label, typeof s.target === "string" ? s.target : "", s.name, tgt.text, tgt.name, tgt.label,
    loc.name, loc.label, loc.text, loc.placeholder, loc.nameAttr, s.text, s.selector);
}

const VERBS = {
  click: "Click", tap: "Click", press: "Press", fill: "Type into", type: "Type into", input: "Type into",
  select: "Choose in", choose: "Choose in", check: "Tick", uncheck: "Untick", navigate: "Go to", goto: "Go to",
  open: "Go to", visit: "Go to", wait: "Wait for", download: "Download", scroll: "Scroll to", hover: "Point at",
  submit: "Submit", extract: "Read", read: "Read", assert: "Check that", verify: "Check that",
};

// -> {verb, target, value, ask, params: [names], danger, raw}
// value is "" when nothing is typed; params lists {{refs}} found in value.
export function describeStep(s, params = []) {
  if (!isObj(s)) return { verb: "Step", target: "", value: "", ask: false, params: [], danger: false };
  const kind = firstText(s.kind, s.type, s.action).toLowerCase();
  const verb = VERBS[kind] || (kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : "Step");
  let target = stepTarget(s);
  if (!target && (kind === "navigate" || kind === "goto" || kind === "open" || kind === "visit")) {
    const u = safeHttpUrl(s.url);
    target = u ? u.host + (u.pathname === "/" ? "" : u.pathname) : "";
  }
  const ask = s.ask === true;
  const raw = stepValue(s);
  const refs = paramRefs(raw);
  const labels = Object.fromEntries(params.map((p) => [p.name, p.label || p.name]));
  let value = "";
  if (ask) value = "you type it each time";
  else if (refs.length) value = raw.replace(PARAM_REF, (_, n) => `‹${labels[n] || n}›`).slice(0, 200);
  else if (raw) value = s.sensitive || /\{\{\s*secret:/.test(raw) ? "a value you enter (kept private)" : raw.slice(0, 200);
  return { verb, target, value, ask, params: refs, danger: Boolean(s.danger) };
}

// One sentence, for copy/paste and tests.
export function stepSentence(s, params = []) {
  const d = describeStep(s, params);
  let line = d.verb + (d.target ? ` “${d.target}”` : "");
  if (d.value) line += d.ask ? ` (${d.value})` : ` with ${d.value}`;
  return line;
}

export function checkLines(checks) {
  const out = [];
  if (!checks) return out;
  if (checks.goal) out.push(`Goal: ${checks.goal}`);
  for (const t of checks.expect_text || []) out.push(`The page shows “${t}”`);
  if (checks.expect_url) out.push(`The address matches ${checks.expect_url}`);
  if (checks.expect_download) out.push(`A file is downloaded whose name matches ${checks.expect_download.name_regex}`);
  return out;
}
