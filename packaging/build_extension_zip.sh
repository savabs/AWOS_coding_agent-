#!/bin/bash
# build_extension_zip.sh — the Chrome Web Store upload zip for AWOS Replay (extension/).
#
#     bash packaging/build_extension_zip.sh            # validate, then dist/awos-replay-<version>.zip
#     bash packaging/build_extension_zip.sh --check    # validate + print permissions only, no zip
#     bash packaging/build_extension_zip.sh --strict   # also fail on store blockers (no 128px icon, "MVP" in
#                                                      # the name, description > 132 chars): use for the upload
#
# Validates extension/manifest.json (MV3, Chrome version format, description <= 132 chars, every file it
# names exists, no remote code), prints every permission / host / content-script match with its store
# justification from packaging/permission_justifications.json (a permission without one fails the build),
# and writes a reproducible zip (sorted, fixed timestamps) without tests, docs, source maps or dotfiles,
# plus dist/awos-replay-<version>.permissions.md to paste into the store's Privacy practices tab.
# Env: AWOS_EXT_DIR (extension/), AWOS_DIST_DIR (dist/).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXT="${AWOS_EXT_DIR:-$ROOT/extension}"
OUT="${AWOS_DIST_DIR:-$ROOT/dist}"
JUST="$ROOT/packaging/permission_justifications.json"
MODE="${1:-build}"
case "$MODE" in build|--check|--strict) ;; *) echo "usage: $0 [--check|--strict]"; exit 2 ;; esac
[ -f "$EXT/manifest.json" ] || { echo "no manifest.json in $EXT"; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

python3 -I - "$EXT" "$OUT" "$JUST" "$MODE" <<'PY'
import hashlib, json, os, re, sys, zipfile
ext, out, just_path, mode = sys.argv[1:5]
errors, blockers = [], []
try:
    m = json.load(open(os.path.join(ext, "manifest.json")))
except ValueError as e:
    sys.exit(f"manifest.json is not valid JSON: {e}")
just = json.load(open(just_path))

if m.get("manifest_version") != 3: errors.append("manifest_version must be 3 (MV3)")
for k in ("name", "version", "description"):
    if not m.get(k): errors.append(f"missing '{k}'")
ver = str(m.get("version", ""))
if not re.fullmatch(r"\d+(\.\d+){0,3}", ver) or any(int(p) > 65535 or (len(p) > 1 and p[0] == "0") for p in ver.split(".")):
    errors.append(f"version '{ver}' is not 1-4 dot-separated integers 0-65535 without leading zeros")
if len(m.get("description", "")) > 132:   # Chrome loads it; the Web Store rejects the upload
    blockers.append(f"description is {len(m['description'])} chars; the store allows 132")
if len(m.get("name", "")) > 75: errors.append("name is longer than 75 chars")
if "MVP" in m.get("name", "") or "beta" in m.get("name", "").lower():
    blockers.append(f"name '{m['name']}' says MVP/beta: the store listing title comes from it")
if "background" in m and ("scripts" in m["background"] or "page" in m["background"]):
    errors.append("MV3 needs background.service_worker, not scripts/page")
if "content_security_policy" in m and re.search(r"https?:", json.dumps(m["content_security_policy"])):
    errors.append("content_security_policy allows remote script sources (MV3 forbids remote code)")

# every file the manifest names must exist in the package
named = []
bg = m.get("background", {}).get("service_worker")
if bg: named.append(bg)
for cs in m.get("content_scripts", []): named += cs.get("js", []) + cs.get("css", [])
act = m.get("action", {})
if act.get("default_popup"): named.append(act["default_popup"])
if m.get("options_page"): named.append(m["options_page"])
if m.get("options_ui", {}).get("page"): named.append(m["options_ui"]["page"])
if m.get("side_panel", {}).get("default_path"): named.append(m["side_panel"]["default_path"])
icons = dict(m.get("icons", {}))
named += list(icons.values()) + list((act.get("default_icon") or {}).values() if isinstance(act.get("default_icon"), dict) else [])
for war in m.get("web_accessible_resources", []):
    named += [r for r in war.get("resources", []) if "*" not in r]
for f in named:
    if not os.path.isfile(os.path.join(ext, f)): errors.append(f"manifest names '{f}' but it is not in {ext}")
if "128" not in icons: blockers.append("no icons.128 in manifest.json (the store needs a 128x128 PNG icon)")

# permissions: printed with their justification; a missing justification is an error
rows = []
for kind, key, items in (("permission", "permissions", m.get("permissions", [])),
                         ("optional permission", "permissions", m.get("optional_permissions", [])),
                         ("host permission", "host_permissions", m.get("host_permissions", [])),
                         ("optional host", "host_permissions", m.get("optional_host_permissions", [])),
                         ("content script match", "content_script_matches",
                          sorted({p for cs in m.get("content_scripts", []) for p in cs.get("matches", [])}))):
    for p in items:
        j = just.get(key, {}).get(p)
        if not j: errors.append(f"{kind} '{p}' has no justification in packaging/permission_justifications.json")
        rows.append((kind, p, j or "MISSING"))

print(f"manifest: {m.get('name')} {ver} (MV{m.get('manifest_version')})")
print("permissions requested:")
for kind, p, j in rows: print(f"  {kind:22s} {p}")
for b in blockers: print(f"STORE BLOCKER: {b}")
if mode == "--strict": errors += blockers
if errors:
    for e in errors: print(f"ERROR: {e}")
    sys.exit(1)
if mode == "--check":
    print("manifest OK (no zip built: --check)"); sys.exit(0)

# reproducible zip: sorted names, fixed timestamp, 0644; dev/test files left out
SKIP_DIRS = {"tests", "test", "__tests__", "node_modules", "dev", "docs", "fixtures", "coverage"}
SKIP_RE = re.compile(r"(^\.|\.test\.js$|\.spec\.js$|\.map$|\.md$|\.log$|\.zip$|^package(-lock)?\.json$|^tsconfig.*\.json$|\.ts$)")
files = []
for d, dirs, names in os.walk(ext):
    dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
    for n in names:
        if not SKIP_RE.search(n): files.append(os.path.relpath(os.path.join(d, n), ext))
files.sort()
os.makedirs(out, exist_ok=True)
base = "awos-replay-" + ver
zpath = os.path.join(out, base + ".zip")
with zipfile.ZipFile(zpath + ".tmp", "w", zipfile.ZIP_DEFLATED) as z:
    for f in files:
        zi = zipfile.ZipInfo(f, date_time=(2020, 1, 1, 0, 0, 0))
        zi.external_attr = 0o644 << 16
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, open(os.path.join(ext, f), "rb").read())
os.replace(zpath + ".tmp", zpath)
with open(os.path.join(out, base + ".permissions.md"), "w") as fh:
    fh.write(f"# AWOS Replay {ver}: permission justifications (generated by packaging/build_extension_zip.sh)\n\n")
    fh.write("| Kind | Permission | Why AWOS Replay needs it |\n|---|---|---|\n")
    for kind, p, j in rows: fh.write(f"| {kind} | `{p}` | {j} |\n")
sha = hashlib.sha256(open(zpath, "rb").read()).hexdigest()
print(f"packaged {len(files)} files: " + ", ".join(files))
print(f"zip: {zpath} ({os.path.getsize(zpath)} bytes, sha256 {sha})")
print(f"justifications: {os.path.join(out, base + '.permissions.md')}")
PY
