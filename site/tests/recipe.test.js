// node --test site/tests/   (Node >= 20; no dependencies)
// The /r/ recipe-link logic: decode (base64url + deflate-raw), validate the v1
// contract, describe steps in plain language. Same module the browser loads.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { deflateRawSync } from "node:zlib";

import {
  encodeRecipe, decodeFragment, validateRecipe, readRecipeLink, describeStep, stepSentence,
  checkLines, base64urlEncode, base64urlDecode, MAX_BYTES, RecipeError,
} from "../src/assets/recipe-core.js";

const here = new URL(".", import.meta.url);
const examples = JSON.parse(readFileSync(new URL("../src/recipes/examples.json", here), "utf-8"));

const base = () => ({
  awos_recipe: 1, name: "Download invoice", description: "Saves the PDF.",
  start_url: "https://billing.example.com/account",
  params: [{ name: "month", label: "Month", example: "October 2026", kind: "text" }],
  steps: [
    { action: "click", loc: { tag: "a", name: "Invoices" } },
    { action: "select", loc: { label: "Billing period" }, value: "{{month}}" },
    { kind: "fill", label: "Notes", ask: true },
  ],
  checks: { goal: "PDF saved", expect_text: ["Invoice"], expect_url: null, expect_download: { name_regex: "\\.pdf$" } },
  created_with: "test",
});

// Python's build.py uses zlib raw deflate + urlsafe b64 without padding; Node's zlib is the same format.
const nodeFragment = (obj) => base64urlEncode(deflateRawSync(Buffer.from(JSON.stringify(obj))));

test("round trip: encodeRecipe -> readRecipeLink gives back the recipe", async () => {
  const frag = await encodeRecipe(base());
  assert.match(frag, /^[A-Za-z0-9_-]+$/);
  const res = await readRecipeLink("#" + frag);
  assert.equal(res.ok, true, JSON.stringify(res.errors));
  assert.equal(res.recipe.name, "Download invoice");
  assert.equal(res.recipe.steps.length, 3);
  assert.deepEqual(res.recipe.checks.expect_download, { name_regex: "\\.pdf$" });
});

test("decode accepts a full URL, a bare fragment, padding, and zlib-made (build.py-style) data", async () => {
  const frag = nodeFragment(base());
  for (const input of [frag, "#" + frag, "https://awos.tirramind.com/r/#" + frag, frag + "=".repeat((4 - frag.length % 4) % 4)]) {
    assert.equal((await decodeFragment(input)).name, "Download invoice");
  }
});

test("every example recipe in src/recipes/examples.json validates and round-trips", async () => {
  assert.equal(examples.length, 6);
  for (const ex of examples) {
    const res = await readRecipeLink("#" + nodeFragment(ex.recipe));
    assert.equal(res.ok, true, `${ex.slug}: ${JSON.stringify(res.errors)}`);
    assert.match(new URL(ex.recipe.start_url).hostname, /(^|\.)example\.(com|org|net)$/, `${ex.slug} uses a placeholder site`);
  }
});

test("the built gallery's /r/# links decode in the browser code (Python-encoded)", async (t) => {
  const built = new URL("../public/recipes/index.html", here);
  if (!existsSync(built)) return t.skip("run python3 site/build.py first");
  const links = [...readFileSync(built, "utf-8").matchAll(/href="\/r\/#([^"]+)"/g)].map((m) => m[1]);
  assert.equal(links.length, 6);
  for (const frag of links) assert.equal((await readRecipeLink("#" + frag)).ok, true);
});

test("rejects: empty, damaged, not JSON, wrong version, not a recipe", async () => {
  const bad = async (input) => {
    const r = await readRecipeLink(input);
    assert.equal(r.ok, false);
    assert.ok(r.errors.length && typeof r.errors[0] === "string");
    return r.errors[0];
  };
  assert.match(await bad(""), /no recipe/);
  assert.match(await bad("#"), /no recipe/);
  assert.match(await bad("#abc$%^"), /damaged/);
  assert.match(await bad("#" + base64urlEncode(new TextEncoder().encode("plain bytes, not deflate"))), /damaged/);
  assert.match(await bad("#" + base64urlEncode(deflateRawSync(Buffer.from("{not json")))), /not valid JSON/);
  assert.match(await bad("#" + nodeFragment({ ...base(), awos_recipe: 2 })), /version 2/);
  assert.match(await bad("#" + nodeFragment({ ...base(), awos_recipe: "1" })), /version "1"/);
  assert.match(await bad("#" + nodeFragment({ name: "x" })), /not an AWOS Replay recipe/);
  assert.match(await bad("#" + nodeFragment([1, 2])), /not an AWOS Replay recipe/);
  // truncated link
  const frag = nodeFragment(base());
  assert.match(await bad("#" + frag.slice(0, frag.length - 9)), /damaged/);
});

test("rejects recipes over 64 KB, both long links and small links that inflate past the cap", async () => {
  const big = { ...base(), description: "x".repeat(MAX_BYTES) };
  // Highly compressible: the link is short, the JSON is > 64 KB (zip-bomb shape).
  const frag = nodeFragment(big);
  assert.ok(frag.length < 2000);
  const r = await readRecipeLink("#" + frag);
  assert.equal(r.ok, false);
  assert.match(r.errors[0], /larger than 64 KB/);
  // A fragment longer than any 64 KB recipe could need is refused before decoding.
  const long = await readRecipeLink("#" + "A".repeat(MAX_BYTES * 2));
  assert.match(long.errors[0], /larger than 64 KB/);
});

test("validation: required fields, types, URL scheme, params, checks, undeclared {{param}}", () => {
  const errs = (patch) => {
    const r = validateRecipe({ ...base(), ...patch });
    assert.equal(r.ok, false, `expected errors for ${JSON.stringify(patch)}`);
    return r.errors.join(" | ");
  };
  assert.match(errs({ name: "" }), /"name" is missing/);
  assert.match(errs({ name: 42 }), /"name" must be text/);
  assert.match(errs({ start_url: "javascript:alert(1)" }), /http or https/);
  assert.match(errs({ start_url: "file:///etc/passwd" }), /http or https/);
  assert.match(errs({ steps: [] }), /non-empty list/);
  assert.match(errs({ steps: "click" }), /non-empty list/);
  assert.match(errs({ steps: [1] }), /steps\[0\] must be an object/);
  assert.match(errs({ params: {} }), /"params" must be a list/);
  assert.match(errs({ params: [{ name: "a b" }] }), /letters, digits/);
  assert.match(errs({ params: [{ name: "m", kind: "password" }] }), /kind must be "text"/);
  assert.match(errs({ params: [{ name: "m" }, { name: "m" }] }), /listed twice/);
  assert.match(errs({ checks: [] }), /"checks" must be an object/);
  assert.match(errs({ checks: { expect_text: "Invoice" } }), /expect_text/);
  assert.match(errs({ checks: { expect_download: "pdf" } }), /expect_download/);
  assert.match(errs({ params: [] }), /\{\{month\}\} but no param/);
  // Optional fields may be missing.
  const min = validateRecipe({ awos_recipe: 1, name: "n", start_url: "https://example.com/", steps: [{ action: "click" }] });
  assert.equal(min.ok, true);
  assert.deepEqual(min.recipe.params, []);
  assert.deepEqual(min.recipe.checks, { goal: "", expect_text: [], expect_url: null, expect_download: null });
});

test("describeStep: kind/type/action, label/name/target/loc text, {{param}} and ask", () => {
  const params = [{ name: "month", label: "Month" }];
  assert.equal(stepSentence({ action: "click", loc: { name: "Download PDF" } }), "Click “Download PDF”");
  assert.equal(stepSentence({ type: "fill", label: "Email" , value: "a@example.com" }), "Type into “Email” with a@example.com");
  assert.equal(stepSentence({ kind: "select", target: { text: "Period" }, value: "{{month}}" }, params), "Choose in “Period” with ‹Month›");
  assert.equal(stepSentence({ kind: "fill", name: "Notes", ask: true }), "Type into “Notes” (you type it each time)");
  assert.equal(stepSentence({ action: "fill", loc: { label: "Password" }, value: "{{secret:password}}", sensitive: "password" }),
    "Type into “Password” with a value you enter (kept private)");
  assert.equal(stepSentence({ action: "goto", url: "https://shop.example.net/orders" }), "Go to “shop.example.net/orders”");
  assert.equal(stepSentence({ action: "teleport" }), "Teleport");
  assert.equal(stepSentence({}), "Step");
  const d = describeStep({ action: "click", loc: { name: "Save" }, danger: true });
  assert.equal(d.danger, true);
  assert.deepEqual(describeStep({ value: "{{a}} and {{ b }}" }).params, ["a", "b"]);
});

test("describeStep returns plain strings: markup in a recipe stays text", () => {
  const evil = '<img src=x onerror="alert(1)">';
  const d = describeStep({ action: evil, label: evil, value: evil });
  for (const v of [d.verb, d.target, d.value]) assert.equal(typeof v, "string");
  assert.ok(d.target.includes("<img"));      // unchanged text; the page renders it with textContent
  assert.ok(describeStep({ label: "x".repeat(5000) }).target.length <= 120);
});

test("checkLines: plain-language checks", () => {
  assert.deepEqual(checkLines(base().checks), [
    "Goal: PDF saved", "The page shows “Invoice”", "A file is downloaded whose name matches \\.pdf$"]);
  assert.deepEqual(checkLines({ goal: "", expect_text: [], expect_url: "/done", expect_download: null }),
    ["The address matches /done"]);
});

test("base64url helpers", () => {
  const bytes = new Uint8Array([251, 255, 0, 62, 63]);
  const s = base64urlEncode(bytes);
  assert.doesNotMatch(s, /[+/=]/);
  assert.deepEqual(base64urlDecode(s), bytes);
  assert.throws(() => base64urlDecode("a"), RecipeError);
});

test("recipe page scripts never write HTML from strings", () => {
  for (const f of ["recipe-core.js", "recipe-page.js"]) {
    const code = readFileSync(new URL(`../src/assets/${f}`, here), "utf-8")
      .split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");
    assert.doesNotMatch(code, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\b|new Function/, f);
  }
});
