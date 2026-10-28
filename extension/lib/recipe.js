/* AWOS Replay: recipe format v1 (docs/specs/recipe_v1.md is the source of truth).
 *
 *  fromRecording(rec) -> {recipe, warnings}   export: typed values become {{param}} or ask:true, secrets are
 *                                              always ask, typed values are scrubbed from every other field,
 *                                              URL queries/fragments stripped; throws if invalid or > 64 KB.
 *  validate(obj)      -> {ok, recipe, warnings} | {ok:false, errors}   (normalises: unknown keys dropped)
 *  toRecording(recipe, id) -> an untrusted internal recording (imported:true, domain-locked to start_url's site)
 *  encodeLink(recipe) / decodeLink(linkOrFragment)   share link: base64url(deflate-raw(JSON)) in the fragment
 *  registrableDomain(host), sameSite(url, domain), substitute(text, values), paramTokens(text)
 * Plain script: defines globalThis.AWOSRecipe (service worker, popup, test pages). Needs AWOSSafety for export.
 */
(function (root) {
  'use strict';

  var VERSION = 1, MAX_BYTES = 65536, CREATED_WITH = 'awos-replay 0.4';
  var SHARE_BASE = 'https://awos.tirramind.com/r/#';
  var PARAM_RE = /^[a-z][a-z0-9_]{0,31}$/;
  var TOKEN_RE = /^\{\{([a-z][a-z0-9_]{0,31})\}\}$/;
  var TOKEN_G = /\{\{([a-z][a-z0-9_]{0,31})\}\}/g;
  var KINDS = { text: 1, number: 1, date: 1 };
  var ACTIONS = { goto: 1, click: 1, fill: 1, select: 1, press: 1 };
  var LOC_STR = ['tag', 'type', 'role', 'name', 'context', 'block', 'text', 'id', 'nameAttr', 'placeholder', 'label',
                 'href', 'css', 'classes', 'neighbor', 'title', 'formId'];
  var MIN_SCRUB = 3;          // typed values shorter than this are dropped from the step but not hunted elsewhere

  function str(v) { return v == null ? '' : String(v); }
  function isObj(v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
  function utf8Len(s) { return new TextEncoder().encode(s).length; }

  /** origin + path; '' for anything that is not an http(s) URL. */
  function stripUrl(u) {
    try { var x = new URL(String(u)); return /^https?:$/.test(x.protocol) ? x.origin + x.pathname : ''; } catch (e) { return ''; }
  }
  function httpUrl(u) { try { return /^https?:$/.test(new URL(String(u)).protocol); } catch (e) { return false; } }

  // ---- registrable domain (eTLD+1) ---------------------------------------------------------------------
  // No public-suffix list in the extension: last two labels, or three under the common two-label suffixes.
  // Errs towards a NARROWER site (e.g. foo.github.io stays foo.github.io), which only means more prompts.
  var TWO_LABEL = /^(co|com|net|org|gov|edu|ac|ltd|plc|me|sch|nhs|police|mod|gob|go|or|ne|nic)\.[a-z]{2}$/;
  var SHARED_HOSTS = /^(github\.io|pages\.dev|netlify\.app|vercel\.app|herokuapp\.com|blogspot\.com|appspot\.com|azurewebsites\.net|cloudfront\.net|web\.app|firebaseapp\.com|workers\.dev)$/;
  function registrableDomain(host) {
    host = str(host).toLowerCase().replace(/\.$/, '');
    if (!host || /^[\d.]+$/.test(host) || host.indexOf(':') >= 0 || host.indexOf('.') < 0) return host;   // IP, [::1], localhost
    var parts = host.split('.'), n = parts.length;
    var last2 = parts.slice(-2).join('.');
    if ((TWO_LABEL.test(last2) || SHARED_HOSTS.test(last2)) && n >= 3) return parts.slice(-3).join('.');
    return last2;
  }
  function hostOf(u) { try { return new URL(String(u)).hostname; } catch (e) { return ''; } }
  function siteOf(u) { return registrableDomain(hostOf(u)); }
  function sameSite(url, domain) { var d = siteOf(url); return !!d && d === str(domain).toLowerCase(); }

  // ---- params ---------------------------------------------------------------------------------------------
  function paramTokens(text) { var out = [], m; TOKEN_G.lastIndex = 0; while ((m = TOKEN_G.exec(str(text)))) out.push(m[1]); return out; }
  function substitute(text, values) {
    return str(text).replace(TOKEN_G, function (all, name) { return values && values[name] != null ? String(values[name]) : all; });
  }

  // ---- validation -----------------------------------------------------------------------------------------
  function capStr(v, n) { return typeof v === 'string' && v.length <= n; }

  function normLoc(loc, where, errors) {
    if (!isObj(loc)) { errors.push(where + ': loc must be an object'); return null; }
    var out = {};
    LOC_STR.forEach(function (k) {
      if (loc[k] == null) return;
      if (!capStr(loc[k], 400)) { errors.push(where + ': loc.' + k + ' must be a string of at most 400 chars'); return; }
      out[k] = loc[k];
    });
    if (out.href && /[?#]/.test(out.href)) errors.push(where + ': loc.href must not have a query or fragment');
    if (loc.siblings != null) {
      if (!Array.isArray(loc.siblings) || loc.siblings.length > 12 || !loc.siblings.every(function (s) { return capStr(s, 400); }))
        errors.push(where + ': loc.siblings must be up to 12 strings');
      else out.siblings = loc.siblings.slice();
    }
    if (loc.pos != null) {
      var p = loc.pos;
      if (!isObj(p) || !['x', 'y', 'w', 'h'].every(function (k) { return typeof p[k] === 'number' && isFinite(p[k]); }))
        errors.push(where + ': loc.pos must be {x, y, w, h} numbers');
      else out.pos = { x: p.x, y: p.y, w: p.w, h: p.h };
    }
    return out;
  }

  function cleanUrlField(u, where, errors) {
    if (!capStr(u, 2000) || !httpUrl(u)) { errors.push(where + ' must be an http(s) URL'); return null; }
    if (/[?#]/.test(u)) { errors.push(where + ' must not have a query string or fragment'); return null; }
    return u;
  }

  function validate(obj) {
    var errors = [], warnings = [];
    if (!isObj(obj)) return { ok: false, errors: ['recipe must be a JSON object'] };
    var size;
    try { size = utf8Len(JSON.stringify(obj)); } catch (e) { return { ok: false, errors: ['recipe is not serialisable JSON'] }; }
    if (size > MAX_BYTES) return { ok: false, errors: ['recipe is ' + size + ' bytes; the limit is 64 KB'] };
    if (obj.awos_recipe !== VERSION) errors.push('awos_recipe must be 1');
    if (typeof obj.name !== 'string' || !obj.name.trim() || obj.name.length > 200) errors.push('name must be 1-200 chars');
    if (obj.description != null && !capStr(obj.description, 2000)) errors.push('description must be a string of at most 2000 chars');
    var start = cleanUrlField(obj.start_url, 'start_url', errors);
    if (obj.created_with != null && !capStr(obj.created_with, 60)) errors.push('created_with must be a short string');

    var params = [], declared = {};
    if (!Array.isArray(obj.params) || obj.params.length > 20) errors.push('params must be an array of at most 20');
    else obj.params.forEach(function (p, k) {
      var w = 'params[' + k + ']';
      if (!isObj(p)) { errors.push(w + ' must be an object'); return; }
      if (typeof p.name !== 'string' || !PARAM_RE.test(p.name)) { errors.push(w + '.name must match ' + PARAM_RE.source); return; }
      if (declared[p.name]) { errors.push(w + '.name "' + p.name + '" is repeated'); return; }
      if (typeof p.label !== 'string' || !p.label.trim() || p.label.length > 100) errors.push(w + '.label must be 1-100 chars');
      if (p.example != null && !capStr(p.example, 200)) errors.push(w + '.example must be at most 200 chars');
      var kind = p.kind == null ? 'text' : p.kind;
      if (!KINDS[kind]) errors.push(w + '.kind must be text, number or date');
      declared[p.name] = 1;
      params.push({ name: p.name, label: str(p.label), example: str(p.example), kind: KINDS[kind] ? kind : 'text' });
    });

    var used = {};
    function useTokens(text, where) {
      paramTokens(text).forEach(function (t) { if (!declared[t]) errors.push(where + ' uses undeclared param {{' + t + '}}'); used[t] = 1; });
    }

    var steps = [];
    if (!Array.isArray(obj.steps) || !obj.steps.length || obj.steps.length > 200) errors.push('steps must be an array of 1-200');
    else obj.steps.forEach(function (s, k) {
      var w = 'steps[' + k + ']';
      if (!isObj(s) || !ACTIONS[s.action]) { errors.push(w + '.action must be goto, click, fill, select or press'); return; }
      if (s.action === 'goto') { var u = cleanUrlField(s.url, w + '.url', errors); if (u) steps.push({ action: 'goto', url: u }); return; }
      var out = { action: s.action, loc: normLoc(s.loc, w, errors) };
      if (s.url != null) { var su = cleanUrlField(s.url, w + '.url', errors); if (su) out.url = su; }
      if (s.action === 'click') {
        if (s.navigates != null) out.navigates = !!s.navigates;
      } else if (s.action === 'press') {                         // Enter in a field (submits its form / search box)
        if (s.key != null && s.key !== 'Enter') errors.push(w + '.key must be "Enter"');
        out.key = 'Enter';
        if (s.navigates != null) out.navigates = !!s.navigates;
      } else if (s.action === 'fill') {
        var hasVal = s.value != null, ask = s.ask === true;
        if (hasVal === ask) errors.push(w + ': a fill has exactly one of value "{{param}}" or ask: true');
        else if (hasVal) {
          var m = TOKEN_RE.exec(typeof s.value === 'string' ? s.value : '');
          if (!m) errors.push(w + '.value must be "{{param}}" (a fill never carries a literal value)');
          else { useTokens(s.value, w + '.value'); out.value = s.value; }
          if (s.sensitive) errors.push(w + ': a sensitive field must be ask: true');
        } else {
          out.ask = true;
          if (s.sensitive != null) {
            if (!capStr(s.sensitive, 40) || !/^[a-z0-9 -]+$/i.test(s.sensitive)) errors.push(w + '.sensitive must be a short reason');
            else out.sensitive = s.sensitive;
          }
        }
      } else {                                                    // select
        if (!capStr(s.value, 400)) errors.push(w + '.value must be a string');
        else { useTokens(s.value, w + '.value'); out.value = s.value; }
        if (s.optionText != null) { if (!capStr(s.optionText, 400)) errors.push(w + '.optionText must be a string'); else out.optionText = s.optionText; }
      }
      steps.push(out);
    });

    var checks = { goal: '', expect_text: [], expect_url: null, expect_download: null };
    var c = obj.checks;
    if (!isObj(c)) errors.push('checks must be an object');
    else {
      if (c.goal != null) { if (!capStr(c.goal, 300)) errors.push('checks.goal must be at most 300 chars'); else { checks.goal = c.goal; useTokens(c.goal, 'checks.goal'); } }
      if (c.expect_text != null) {
        if (!Array.isArray(c.expect_text) || c.expect_text.length > 10 || !c.expect_text.every(function (t) { return typeof t === 'string' && t.length >= 1 && t.length <= 200; }))
          errors.push('checks.expect_text must be up to 10 strings of 1-200 chars');
        else { checks.expect_text = c.expect_text.slice(); c.expect_text.forEach(function (t) { useTokens(t, 'checks.expect_text'); }); }
      }
      if (c.expect_url != null) {
        if (!capStr(c.expect_url, 2000) || /[?#]/.test(c.expect_url) || !/^https?:\/\//.test(c.expect_url)) errors.push('checks.expect_url must be an origin+path pattern without ? or #');
        else { checks.expect_url = c.expect_url; useTokens(c.expect_url, 'checks.expect_url'); }
      }
      if (c.expect_download != null) {
        var d = c.expect_download, ok = isObj(d) && typeof d.name_regex === 'string' && d.name_regex.length >= 1 && d.name_regex.length <= 200;
        if (ok) { try { new RegExp(d.name_regex); } catch (e) { ok = false; } }
        if (!ok) errors.push('checks.expect_download must be null or {name_regex: a valid regex of 1-200 chars}');
        else checks.expect_download = { name_regex: d.name_regex };
      }
    }
    params.forEach(function (p) { if (!used[p.name]) warnings.push('param "' + p.name + '" is not used by any step or check'); });
    if (errors.length) return { ok: false, errors: errors };
    return { ok: true, warnings: warnings, recipe: {
      awos_recipe: VERSION, name: obj.name.trim(), description: str(obj.description), start_url: start, params: params,
      steps: steps, checks: checks, created_with: str(obj.created_with || '') } };
  }

  // ---- export from a recording ---------------------------------------------------------------------------
  function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

  /** rec: {name, description, goal, startUrl, steps[], expect:{urlPattern, texts[]}, params[], expectDownload:{nameRegex}} */
  function fromRecording(rec) {
    var S = root.AWOSSafety, warnings = [];
    var params = (rec.params || []).filter(function (p) { return p && PARAM_RE.test(p.name || ''); });
    var pnames = {}; params.forEach(function (p) { pnames[p.name] = p; });
    // typed values and what replaces them elsewhere: the param token, or nothing
    var typed = [];
    (rec.steps || []).forEach(function (s) {
      if (s.action !== 'fill' || s.value == null || S.isPlaceholder(s.value)) return;
      var v = String(s.value).trim();
      if (v.length >= MIN_SCRUB) typed.push({ value: v, token: s.param && pnames[s.param] && !s.sensitive ? '{{' + s.param + '}}' : null });
    });
    typed.sort(function (a, b) { return b.value.length - a.value.length; });
    function scrub(text, mode) {           // mode: 'loc' (remove), 'goal' (mark), 'check' (null = drop the check)
      var t = str(text), hit = false;
      typed.forEach(function (tv) {
        var re = new RegExp(escRe(tv.value), 'gi');
        if (!re.test(t)) return;
        if (tv.token) t = t.replace(re, tv.token);
        else { hit = true; t = t.replace(re, mode === 'goal' ? '[typed value]' : ''); }
      });
      return mode === 'check' && hit ? null : t;
    }
    function scrubLoc(loc, isFill) {
      var out = {};
      LOC_STR.forEach(function (k) { if (loc && loc[k] != null && loc[k] !== '') out[k] = scrub(String(loc[k]).slice(0, 400), 'loc'); });
      if (out.href) out.href = out.href.split(/[?#]/)[0];
      if (isFill) delete out.text;                         // a contenteditable's text IS the typed value
      if (loc && Array.isArray(loc.siblings)) out.siblings = loc.siblings.slice(0, 12).map(function (s) { return scrub(String(s).slice(0, 400), 'loc'); });
      if (loc && isObj(loc.pos)) out.pos = { x: +loc.pos.x || 0, y: +loc.pos.y || 0, w: +loc.pos.w || 0, h: +loc.pos.h || 0 };
      return out;
    }
    var usedParams = {};
    var steps = (rec.steps || []).map(function (s) {
      if (s.action === 'goto') return { action: 'goto', url: stripUrl(s.url) };
      var out = { action: s.action, loc: scrubLoc(s.loc || {}, s.action === 'fill') };
      var u = stripUrl(s.url); if (u) out.url = u;
      if (s.action === 'click') { if (s.navigates) out.navigates = true; }
      else if (s.action === 'press') { out.key = 'Enter'; if (s.navigates) out.navigates = true; }
      else if (s.action === 'fill') {
        var secret = s.sensitive || S.isPlaceholder(s.value);
        if (!secret && s.param && pnames[s.param]) { out.value = '{{' + s.param + '}}'; usedParams[s.param] = 1; }
        else { out.ask = true; if (secret) out.sensitive = String(s.sensitive || String(s.value).replace(/^\{\{secret:|\}\}$/g, '')); }
        if (secret && s.param) warnings.push('"' + (s.loc && s.loc.name || 'a secret field') + '" is a secret field, so it stays "type it yourself"');
      } else if (s.action === 'select') {
        if (s.param && pnames[s.param]) { out.value = '{{' + s.param + '}}'; usedParams[s.param] = 1; }
        else { out.value = str(s.value); if (s.optionText) out.optionText = String(s.optionText); }
      }
      return out;
    }).filter(function (s) { return s.action !== 'goto' || s.url; });
    var ex = rec.expect || {}, texts = [];
    (ex.texts || []).filter(Boolean).forEach(function (t) {
      var c = scrub(t, 'check');
      if (c === null) warnings.push('dropped the expected text "' + t.slice(0, 40) + '..." because it contains a typed value; make that field a param to keep it');
      else if (c.trim()) texts.push(c.slice(0, 200));
    });
    var urlPat = ex.urlPattern ? String(ex.urlPattern).split(/[?#]/)[0] : '';
    var recipe = {
      awos_recipe: VERSION,
      name: str(rec.name).trim().slice(0, 200) || 'Untitled chore',
      description: scrub(str(rec.description), 'goal').slice(0, 2000),
      start_url: stripUrl(rec.startUrl),
      params: params.filter(function (p) { return usedParams[p.name]; }).map(function (p) {
        return { name: p.name, label: str(p.label || p.name).slice(0, 100), example: str(p.example).slice(0, 200), kind: KINDS[p.kind] ? p.kind : 'text' };
      }),
      steps: steps,
      checks: { goal: scrub(str(rec.goal), 'goal').slice(0, 300), expect_text: texts.slice(0, 10),
                expect_url: /^https?:\/\//.test(urlPat) ? urlPat : null,
                expect_download: rec.expectDownload && rec.expectDownload.nameRegex ? { name_regex: String(rec.expectDownload.nameRegex) } : null },
      created_with: CREATED_WITH,
    };
    var v = validate(recipe);
    if (!v.ok) throw new Error('cannot export: ' + v.errors.join('; '));
    return { recipe: v.recipe, warnings: warnings.concat(v.warnings) };
  }

  // ---- import into an (untrusted) recording ---------------------------------------------------------------
  function toRecording(recipe, id) {
    var S = root.AWOSSafety;
    var steps = recipe.steps.map(function (s) {
      var out = JSON.parse(JSON.stringify(s));
      if (s.action === 'click' || s.action === 'press') {     // never trust a shipped danger flag: recompute from the description (the live gate runs too)
        var l = s.loc || {};
        out.danger = S.dangerMatch([l.name, l.text, l.label, l.title, l.id, l.nameAttr, l.formId].join(' | '));
      }
      if (s.action === 'fill' || s.action === 'select') {
        var m = TOKEN_RE.exec(str(s.value));
        if (m) out.param = m[1];
        if (s.ask) out.value = S.placeholderFor(s.sensitive || 'value');
      }
      return out;
    });
    if (!steps.length || steps[0].action !== 'goto') steps.unshift({ action: 'goto', url: recipe.start_url });   // always start at start_url
    return {
      id: id, name: recipe.name, description: recipe.description, goal: recipe.checks.goal, createdAt: Date.now(),
      startUrl: recipe.start_url, steps: steps, params: recipe.params.slice(),
      expect: { urlPattern: recipe.checks.expect_url || '', texts: recipe.checks.expect_text.slice() },
      expectDownload: recipe.checks.expect_download ? { nameRegex: recipe.checks.expect_download.name_regex } : null,
      imported: true, site: siteOf(recipe.start_url), approvedDomains: [], importedAt: Date.now(),
    };
  }

  // ---- share link -----------------------------------------------------------------------------------------
  function b64url(bytes) {
    var bin = '';
    for (var i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function unb64url(s) {
    if (!/^[A-Za-z0-9_-]*$/.test(s)) throw new Error('not base64url');
    var b = s.replace(/-/g, '+').replace(/_/g, '/');
    var bin = atob(b + '='.repeat((4 - b.length % 4) % 4)), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  async function deflateRaw(text) {
    var stream = new Blob([new TextEncoder().encode(text)]).stream().pipeThrough(new CompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }
  /** inflate, but stop past `limit` bytes (zip-bomb guard). */
  async function inflateRaw(bytes, limit) {
    var reader = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader(), chunks = [], n = 0;
    for (;;) {
      var r = await reader.read();
      if (r.done) break;
      n += r.value.length;
      if (n > limit) { reader.cancel().catch(function () {}); throw new Error('recipe is larger than 64 KB'); }
      chunks.push(r.value);
    }
    var out = new Uint8Array(n), o = 0;
    chunks.forEach(function (c) { out.set(c, o); o += c.length; });
    return new TextDecoder().decode(out);
  }
  async function encodeLink(recipe) {
    var v = validate(recipe);
    if (!v.ok) throw new Error('invalid recipe: ' + v.errors.join('; '));
    return SHARE_BASE + b64url(await deflateRaw(JSON.stringify(v.recipe)));
  }
  /** -> {ok, recipe, warnings} | {ok:false, errors}; accepts a full link or just the fragment. */
  async function decodeLink(input) {
    var s = str(input).trim(), h = s.indexOf('#');
    var frag = h >= 0 ? s.slice(h + 1) : s;
    if (!frag) return { ok: false, errors: ['the link has no recipe after #'] };
    if (frag.length > 120000) return { ok: false, errors: ['the link is too long'] };
    var obj;
    try { obj = JSON.parse(await inflateRaw(unb64url(frag), MAX_BYTES)); }
    catch (e) { return { ok: false, errors: ['cannot read the recipe link: ' + (e && e.message || e)] }; }
    return validate(obj);
  }

  root.AWOSRecipe = {
    VERSION: VERSION, MAX_BYTES: MAX_BYTES, SHARE_BASE: SHARE_BASE, CREATED_WITH: CREATED_WITH, PARAM_RE: PARAM_RE,
    validate: validate, fromRecording: fromRecording, toRecording: toRecording, encodeLink: encodeLink, decodeLink: decodeLink,
    registrableDomain: registrableDomain, siteOf: siteOf, sameSite: sameSite, stripUrl: stripUrl,
    substitute: substitute, paramTokens: paramTokens, b64url: b64url, unb64url: unb64url, deflateRaw: deflateRaw, inflateRaw: inflateRaw,
  };
})(typeof globalThis !== 'undefined' ? globalThis : self);
