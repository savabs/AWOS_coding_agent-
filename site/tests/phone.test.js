// node --test site/tests/   (Node >= 20; no dependencies, no network)
// Phone link protocol v1 (src/assets/phone-core.js) and a full Mac -> relay -> phone round trip.
import { test } from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";

import { b64uEncode, b64uDecode, parsePairing, importKey, seal, open, cleanIncoming, codeReply, approveReply,
  seenReply, DIR_TO_PHONE, DIR_TO_MAC } from "../src/assets/phone-core.js";
import { onRequest, _resetRateMemory } from "../functions/api/relay/[[path]].js";

const rand = (n) => webcrypto.getRandomValues(new Uint8Array(n));

// What the extension generates at pairing.
function newPairing() {
  return {
    c: b64uEncode(rand(16)), k: b64uEncode(rand(32)), t: b64uEncode(rand(16)),
    n: "awos-" + Buffer.from(rand(8)).toString("hex"),
  };
}
const link = (p) => `https://awos.tirramind.com/phone/#c=${p.c}&k=${p.k}&t=${p.t}&n=${p.n}`;

test("base64url: round trip, no padding, rejects junk", () => {
  for (const n of [0, 1, 2, 3, 12, 16, 32, 100]) {
    const b = rand(n);
    const s = b64uEncode(b);
    assert.ok(!/[+/=]/.test(s));
    assert.deepEqual(b64uDecode(s), b);
  }
  assert.equal(b64uEncode(new Uint8Array(16)).length, 22);
  assert.throws(() => b64uDecode("ab+c"));
  assert.throws(() => b64uDecode("a"));
});

test("pairing: accepts the pair link or its fragment; rejects bad parts", () => {
  const p = newPairing();
  assert.deepEqual(parsePairing(link(p)), { ok: true, pair: p });
  assert.deepEqual(parsePairing("#" + link(p).split("#")[1]), { ok: true, pair: p });
  assert.deepEqual(parsePairing(link(p).split("#")[1]), { ok: true, pair: p });
  assert.equal(parsePairing("").ok, false);
  assert.match(parsePairing(link({ ...p, c: "short" })).error, /channel/);
  assert.match(parsePairing(link({ ...p, k: b64uEncode(rand(16)) })).error, /key/);
  assert.match(parsePairing(link({ ...p, t: "" })).error, /SMS token/);
  assert.match(parsePairing(link({ ...p, n: "awos-XYZ" })).error, /topic/);
  assert.match(parsePairing(link({ ...p, n: "other-0123456789abcdef" })).error, /topic/);
});

test("envelope: AES-GCM with the direction as AAD; wrong key or direction fails", async () => {
  const p = newPairing();
  const key = await importKey(p.k);
  const msg = { type: "need", req: "r1", kind: "code", portal: "Acme", message: "Enter the 6-digit code", at: 1 };
  const env = await seal(key, DIR_TO_PHONE, msg);
  assert.deepEqual(Object.keys(env).sort(), ["ct", "id", "iv"]);
  assert.equal(b64uDecode(env.iv).length, 12);
  assert.deepEqual(await open(key, DIR_TO_PHONE, env), msg);
  await assert.rejects(open(key, DIR_TO_MAC, env));
  await assert.rejects(open(await importKey(newPairing().k), DIR_TO_PHONE, env));
  const tampered = { ...env, ct: env.ct.slice(0, -2) + (env.ct.endsWith("AA") ? "BA" : "AA") };
  await assert.rejects(open(key, DIR_TO_PHONE, tampered));
});

test("envelope: decrypts what a plain WebCrypto peer (the extension) seals", async () => {
  const p = newPairing();
  const raw = b64uDecode(p.k);
  const extKey = await webcrypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
  const iv = rand(12);
  const ct = new Uint8Array(await webcrypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode("to-phone") },
    extKey, new TextEncoder().encode(JSON.stringify({ type: "resolved", req: "r9" }))));
  const got = await open(await importKey(p.k), "to-phone", { id: "x", iv: b64uEncode(iv), ct: b64uEncode(ct) });
  assert.deepEqual(got, { type: "resolved", req: "r9" });
  // and the reverse: the extension opens a phone reply
  const reply = await seal(await importKey(p.k), DIR_TO_MAC, codeReply("r9", "482 913"));
  const pt = await webcrypto.subtle.decrypt({ name: "AES-GCM", iv: b64uDecode(reply.iv), additionalData: new TextEncoder().encode("to-mac") },
    extKey, b64uDecode(reply.ct));
  assert.deepEqual(JSON.parse(new TextDecoder().decode(pt)), { type: "code", req: "r9", code: "482913" });
});

test("messages: incoming are checked and trimmed; replies follow v1", () => {
  assert.equal(cleanIncoming(null), null);
  assert.equal(cleanIncoming({ type: "need", req: "r", kind: "hack" }), null);
  assert.equal(cleanIncoming({ type: "need", kind: "code" }), null);
  assert.equal(cleanIncoming({ type: "evil" }), null);
  const need = cleanIncoming({ type: "need", req: "r1", kind: "approve", portal: "P".repeat(500), message: "ok", at: 5, x: 1 });
  assert.deepEqual(Object.keys(need).sort(), ["at", "kind", "message", "portal", "req", "type"]);
  assert.equal(need.portal.length, 120);
  const st = cleanIncoming({ type: "status", summary: "5 of 6 got, 1 needs you", at: 9,
    items: [{ portal: "Acme", state: "got", detail: "invoice.pdf" }, { portal: "Beta", state: "needs you" }] });
  assert.equal(st.items.length, 2);
  assert.equal(st.items[1].detail, "");
  assert.deepEqual(cleanIncoming({ type: "resolved", req: "r1" }), { type: "resolved", req: "r1" });

  assert.deepEqual(codeReply("r1", " 123-456 "), { type: "code", req: "r1", code: "123456" });
  assert.deepEqual(codeReply("r1", "a1b2C3"), { type: "code", req: "r1", code: "a1b2C3" });
  assert.throws(() => codeReply("r1", ""));
  assert.throws(() => codeReply("r1", "1234567890123"));
  assert.throws(() => codeReply("r1", "12<34"));
  assert.deepEqual(approveReply("r2", 0), { type: "approve", req: "r2", yes: false });
  assert.deepEqual(seenReply("r3"), { type: "seen", req: "r3" });
});

test("round trip: Mac seals a need -> relay -> phone opens it -> code reply -> relay -> Mac", async () => {
  _resetRateMemory();
  const kv = new Map();
  const RELAY = {
    async get(k) { return kv.has(k) ? kv.get(k) : null; },
    async put(k, v) { kv.set(k, String(v)); },
    async delete(k) { kv.delete(k); },
  };
  const call = (method, path, body) => onRequest({
    env: { RELAY },
    params: { path: path.split("/") },
    request: new Request(`https://awos.test/api/relay/${path}`, body
      ? { method, body: JSON.stringify(body), headers: { "content-type": "application/json" } } : { method }),
  });
  const p = newPairing();
  const key = await importKey(p.k);
  const need = { type: "need", req: "q1", kind: "code", portal: "Acme Supplies", message: "Code sent by SMS", at: Date.now() };
  assert.equal((await call("POST", `${p.c}/to-phone`, await seal(key, DIR_TO_PHONE, need))).status, 201);

  const { messages } = await (await call("GET", `${p.c}/to-phone`)).json();
  assert.equal(messages.length, 1);
  assert.deepEqual(cleanIncoming(await open(key, DIR_TO_PHONE, messages[0])), need);
  assert.equal((await call("POST", `${p.c}/to-mac`, await seal(key, DIR_TO_MAC, codeReply("q1", "771204")))).status, 201);
  assert.equal((await call("DELETE", `${p.c}/to-phone/${messages[0].id}`)).status, 200);

  const toMac = (await (await call("GET", `${p.c}/to-mac`)).json()).messages;
  assert.deepEqual(await open(key, DIR_TO_MAC, toMac[0]), { type: "code", req: "q1", code: "771204" });
  assert.deepEqual((await (await call("GET", `${p.c}/to-phone`)).json()).messages, []);
  for (const [k, v] of kv) {
    assert.ok(!k.includes(p.c) && !v.includes(p.c) && !v.includes(p.k));
    assert.ok(!v.includes("771204") && !v.includes("Acme"), "relay stored plaintext");
  }
});
