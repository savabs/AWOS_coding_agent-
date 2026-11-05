/* AWOS: a small, dependency-free QR code encoder (ISO/IEC 18004, byte mode).
 *
 * Written for the phone pairing link shown on the options page, roughly 155
 * ASCII chars:
 *   https://awos.tirramind.com/phone/#c=<22 b64url>&k=<43 b64url>&t=<22 b64url>&n=awos-<16 hex>
 * The link carries a pairing secret, so it is drawn locally instead of being
 * sent to a QR web service. No external libs, no eval, no build step.
 *
 *  - AWOSQR.encode(text, ecc, opts) -> { size, version, ecc, mask, modules }
 *      text is UTF-8 encoded, byte mode; ecc 'L'|'M'|'Q'|'H' (default 'M');
 *      the smallest version 1..40 that fits is picked; modules[y][x] true = dark.
 *      opts (testing): { version: v } forces a minimum version, { mask: 0..7 }
 *      forces a mask; otherwise all 8 masks are scored with the standard
 *      penalty rules and the lowest wins. Throws if the text is too long.
 *  - AWOSQR.toSvg(text, opts) -> SVG markup (one <path>, no scripts).
 *      opts: ecc, scale (px per module, 4), margin (quiet zone modules, 4),
 *      dark ('#000'), light ('#fff').
 * Plain script: defines globalThis.AWOSQR (page, content script or worker).
 */
(function (root) {
  'use strict';

  var ECC_IDX = { L: 0, M: 1, Q: 2, H: 3 };
  var ECC_FMT = [1, 0, 3, 2]; // format-info bits for L, M, Q, H
  // EC codewords per block and number of blocks, [ecc][version] (index 0 unused).
  var EC_PER_BLOCK = [
    [0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    [0, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [0, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30]
  ];
  var NUM_BLOCKS = [
    [0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
    [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
    [0, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
    [0, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81]
  ];

  // GF(256) with primitive polynomial x^8+x^4+x^3+x^2+1 (0x11d).
  var EXP = new Array(512), LOG = new Array(256);
  (function () {
    var x = 1;
    for (var i = 0; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11d; }
    for (i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
  })();
  function gmul(a, b) { return a && b ? EXP[LOG[a] + LOG[b]] : 0; }

  function rsGenerator(deg) { // coefficients, highest first, leading 1 omitted
    var g = [1];
    for (var i = 0; i < deg; i++) {
      var n = new Array(g.length + 1);
      for (var j = 0; j < n.length; j++) n[j] = (j < g.length ? g[j] : 0) ^ (j > 0 ? gmul(g[j - 1], EXP[i]) : 0);
      g = n;
    }
    return g.slice(1);
  }
  function rsRemainder(data, gen) {
    var r = gen.map(function () { return 0; });
    for (var i = 0; i < data.length; i++) {
      var f = data[i] ^ r.shift();
      r.push(0);
      for (var j = 0; j < gen.length; j++) r[j] ^= gmul(gen[j], f);
    }
    return r;
  }

  function alignPositions(ver) {
    if (ver === 1) return [];
    var n = Math.floor(ver / 7) + 2, size = ver * 4 + 17;
    var step = ver === 32 ? 26 : Math.ceil((size - 13) / (n * 2 - 2)) * 2;
    var out = [6];
    for (var p = size - 7; out.length < n; p -= step) out.splice(1, 0, p);
    return out;
  }
  function rawModules(ver) { // data + EC modules available
    var r = (16 * ver + 128) * ver + 64;
    if (ver >= 2) { var n = Math.floor(ver / 7) + 2; r -= (25 * n - 10) * n - 55; if (ver >= 7) r -= 36; }
    return r;
  }
  function dataCodewords(ver, e) {
    return Math.floor(rawModules(ver) / 8) - EC_PER_BLOCK[e][ver] * NUM_BLOCKS[e][ver];
  }

  function utf8(text) {
    var s = unescape(encodeURIComponent(String(text))), b = [];
    for (var i = 0; i < s.length; i++) b.push(s.charCodeAt(i));
    return b;
  }

  function encode(text, ecc, opts) {
    ecc = String(ecc || 'M').toUpperCase();
    var e = ECC_IDX[ecc];
    if (e === undefined) throw new Error('QR: bad ECC level ' + ecc);
    opts = opts || {};
    var bytes = utf8(text), ver, cap;
    for (ver = Math.max(1, opts.version | 0); ver <= 40; ver++) {
      cap = dataCodewords(ver, e) * 8;
      if (4 + (ver < 10 ? 8 : 16) + bytes.length * 8 <= cap) break;
    }
    if (ver > 40) throw new Error('QR: text too long (' + bytes.length + ' bytes)');

    // Bit stream: mode 0100, count, data, terminator, pad to byte, pad codewords.
    var bits = [];
    function put(v, n) { for (var i = n - 1; i >= 0; i--) bits.push((v >>> i) & 1); }
    put(4, 4); put(bytes.length, ver < 10 ? 8 : 16);
    bytes.forEach(function (b) { put(b, 8); });
    put(0, Math.min(4, cap - bits.length));
    put(0, (8 - bits.length % 8) % 8);
    for (var pad = 0xec; bits.length < cap; pad ^= 0xec ^ 0x11) put(pad, 8);
    var data = [];
    for (var i = 0; i < bits.length; i += 8) { var c = 0; for (var k = 0; k < 8; k++) c = (c << 1) | bits[i + k]; data.push(c); }

    // Split into blocks, add Reed-Solomon EC, interleave.
    var nb = NUM_BLOCKS[e][ver], ecLen = EC_PER_BLOCK[e][ver], total = Math.floor(rawModules(ver) / 8);
    var nShort = nb - total % nb, shortLen = Math.floor(total / nb) - ecLen, gen = rsGenerator(ecLen);
    var dBlocks = [], eBlocks = [], off = 0;
    for (i = 0; i < nb; i++) {
      var d = data.slice(off, off + shortLen + (i < nShort ? 0 : 1));
      off += d.length; dBlocks.push(d); eBlocks.push(rsRemainder(d, gen));
    }
    var cw = [];
    for (i = 0; i <= shortLen; i++) dBlocks.forEach(function (b) { if (i < b.length) cw.push(b[i]); });
    for (i = 0; i < ecLen; i++) eBlocks.forEach(function (b) { cw.push(b[i]); });

    // Function patterns.
    var size = ver * 4 + 17, m = [], fn = [];
    for (i = 0; i < size; i++) { m.push(new Array(size).fill(false)); fn.push(new Array(size).fill(false)); }
    function set(x, y, dark) { m[y][x] = !!dark; fn[y][x] = true; }
    for (i = 0; i < size; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
    [[3, 3], [size - 4, 3], [3, size - 4]].forEach(function (p) {
      for (var dy = -4; dy <= 4; dy++) for (var dx = -4; dx <= 4; dx++) {
        var x = p[0] + dx, y = p[1] + dy, dist = Math.max(Math.abs(dx), Math.abs(dy));
        if (x >= 0 && x < size && y >= 0 && y < size) set(x, y, dist !== 2 && dist !== 4);
      }
    });
    var al = alignPositions(ver);
    al.forEach(function (ay, a) {
      al.forEach(function (ax, b) {
        if ((a === 0 && b === 0) || (a === 0 && b === al.length - 1) || (a === al.length - 1 && b === 0)) return;
        for (var dy = -2; dy <= 2; dy++) for (var dx = -2; dx <= 2; dx++) set(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
      });
    });
    function drawFormat(mask) {
      var d = (ECC_FMT[e] << 3) | mask, r = d;
      for (var j = 0; j < 10; j++) r = (r << 1) ^ ((r >>> 9) * 0x537);
      var f = ((d << 10) | r) ^ 0x5412, bit = function (j) { return (f >>> j) & 1; };
      for (j = 0; j <= 5; j++) set(8, j, bit(j));
      set(8, 7, bit(6)); set(8, 8, bit(7)); set(7, 8, bit(8));
      for (j = 9; j < 15; j++) set(14 - j, 8, bit(j));
      for (j = 0; j < 8; j++) set(size - 1 - j, 8, bit(j));
      for (j = 8; j < 15; j++) set(8, size - 15 + j, bit(j));
      set(8, size - 8, 1); // dark module
    }
    drawFormat(0); // reserve the area
    if (ver >= 7) {
      var r = ver;
      for (i = 0; i < 12; i++) r = (r << 1) ^ ((r >>> 11) * 0x1f25);
      var vb = (ver << 12) | r;
      for (i = 0; i < 18; i++) {
        var a = size - 11 + i % 3, b = Math.floor(i / 3), v = (vb >>> i) & 1;
        set(a, b, v); set(b, a, v);
      }
    }

    // Data placement in the zig-zag order.
    var idx = 0;
    for (var right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (var vert = 0; vert < size; vert++) {
        for (var j = 0; j < 2; j++) {
          var x = right - j, up = ((right + 1) & 2) === 0, y = up ? size - 1 - vert : vert;
          if (!fn[y][x] && idx < cw.length * 8) { m[y][x] = ((cw[idx >>> 3] >>> (7 - (idx & 7))) & 1) === 1; idx++; }
        }
      }
    }

    var MASKS = [
      function (x, y) { return (x + y) % 2 === 0; },
      function (x, y) { return y % 2 === 0; },
      function (x) { return x % 3 === 0; },
      function (x, y) { return (x + y) % 3 === 0; },
      function (x, y) { return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; },
      function (x, y) { return x * y % 2 + x * y % 3 === 0; },
      function (x, y) { return (x * y % 2 + x * y % 3) % 2 === 0; },
      function (x, y) { return ((x + y) % 2 + x * y % 3) % 2 === 0; }
    ];
    function applyMask(k) {
      for (var y = 0; y < size; y++) for (var x = 0; x < size; x++) if (!fn[y][x] && MASKS[k](x, y)) m[y][x] = !m[y][x];
    }
    var best = opts.mask, bestScore = Infinity;
    if (best === undefined || best === null) {
      for (var k = 0; k < 8; k++) {
        applyMask(k); drawFormat(k);
        var s = penalty(m, size);
        if (s < bestScore) { bestScore = s; best = k; }
        applyMask(k); // XOR undo
      }
    }
    applyMask(best); drawFormat(best);
    return { size: size, version: ver, ecc: ecc, mask: best, modules: m };
  }

  // Penalty rules N1..N4 (ISO 18004 7.8.3), scored the way Nayuki's widely used
  // qrcodegen does (run-length finder detection, light border counts as light),
  // so the auto-picked mask matches that reference.
  function penalty(m, size) {
    var score = 0, dark = 0, x, y;
    function addHistory(len, h) { if (h[0] === 0) len += size; h.pop(); h.unshift(len); }
    function countFinders(h) {
      var n = h[1], core = n > 0 && h[2] === n && h[3] === n * 3 && h[4] === n && h[5] === n;
      return (core && h[0] >= n * 4 && h[6] >= n ? 1 : 0) + (core && h[6] >= n * 4 && h[0] >= n ? 1 : 0);
    }
    function lines(get) {
      for (var a = 0; a < size; a++) {
        var color = false, run = 0, h = [0, 0, 0, 0, 0, 0, 0];
        for (var b = 0; b < size; b++) {
          if (get(a, b) === color) { run++; if (run === 5) score += 3; else if (run > 5) score++; }
          else { addHistory(run, h); if (!color) score += countFinders(h) * 40; color = get(a, b); run = 1; }
        }
        if (color) { addHistory(run, h); run = 0; }
        addHistory(run + size, h);
        score += countFinders(h) * 40;
      }
    }
    lines(function (a, b) { return m[a][b]; });
    lines(function (a, b) { return m[b][a]; });
    for (y = 0; y < size; y++) for (x = 0; x < size; x++) {
      if (m[y][x]) dark++;
      if (x < size - 1 && y < size - 1 && m[y][x] === m[y][x + 1] && m[y][x] === m[y + 1][x] && m[y][x] === m[y + 1][x + 1]) score += 3;
    }
    var total = size * size;
    score += (Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1) * 10;
    return score;
  }

  function esc(s) { return String(s).replace(/[^#0-9a-zA-Z(),.% -]/g, ''); }

  function toSvg(text, opts) {
    opts = opts || {};
    var q = encode(text, opts.ecc), s = opts.scale > 0 ? +opts.scale : 4;
    var mg = opts.margin >= 0 ? Math.floor(opts.margin) : 4, n = q.size + mg * 2, d = [];
    for (var y = 0; y < q.size; y++) for (var x = 0; x < q.size; x++) {
      if (q.modules[y][x]) d.push('M' + (x + mg) + ',' + (y + mg) + 'h1v1h-1z');
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + n * s + '" height="' + n * s +
      '" viewBox="0 0 ' + n + ' ' + n + '" shape-rendering="crispEdges">' +
      '<rect width="100%" height="100%" fill="' + esc(opts.light || '#fff') + '"/>' +
      '<path fill="' + esc(opts.dark || '#000') + '" d="' + d.join('') + '"/></svg>';
  }

  root.AWOSQR = { encode: encode, toSvg: toSvg, penalty: penalty };
})(typeof globalThis !== 'undefined' ? globalThis : self);
