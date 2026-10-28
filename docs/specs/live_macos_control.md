# Live macOS control layer (real computer use)

Status: built and unit-tested on a fake AX runtime (2026-10-20). Not yet run against real
Safari. The first real run happens inside the guest VM once the owner grants the guest's
AWOS Helper.app Accessibility.

Code: `scaffold/agent/desktop/live/` (`observe.py`, `actions.py`, `driver.py`, `probe.py`,
`__main__.py`). Tests: `tests/test_live_driver.py`.

## 1. Where it runs: only in the guest VM

The owner decided on 2026-10-20 that **AI does not control the host Mac at all**. The live
driver runs inside a sandboxed macOS guest: Tart on Apple Virtualization.framework, guest user
`admin`, reached over SSH at the address `tart ip <name>` returns.

- Every acting path calls `live.in_guest_vm()`, which reads `sysctl kern.hv_vmm_present`. It
  returns 1 in a guest. On bare metal every action, every demo and the probe's tree dump are
  REFUSED (exit code 2). There is no override.
- The host only runs `vm-run`: it rsyncs the repo into the guest, runs the command over ssh
  inside the guest's AWOS Helper.app, and pulls the results back. The host never needs
  Accessibility or Input Monitoring.
- Inside the guest, UI control runs only inside "AWOS Helper.app". Build it with
  `scripts/macos_helper/build_helper.sh` and launch work through
  `scripts/macos_helper/run_helper.sh`. It is the only app the owner grants permissions to.
  The code checks permissions without prompting (`capture/permissions.check_permissions`,
  `request=False`) and stops cleanly when they are missing. It never triggers a TCC prompt.

## 2. Pieces

| Module | What it does |
|---|---|
| `observe.py` | Takes a snapshot of the frontmost window, or of a named app/window, as a dict tree that `grounding.ground` and `ax_compress.compress` accept. Each node has role, subrole, accessible name (`title`), description, value, placeholder, identifier (AXIdentifier / AXDOMIdentifier), frame, enabled, focused, selected, and `url` for web areas and links. Secure fields get `secure: true` and **never a value**. Limits: node budget (default 2000) and wall-clock timeout (default 8 s). When either is hit the snapshot is marked `truncated` and grounding refuses it. Chromium apps get AXManualAccessibility. Also holds `MacRuntime`, the PyObjC adapter (AX reads/writes/actions, NSRunningApplication activation, `open -a`, CGEvents). |
| `actions.py` | `Actor`: `press`, `set_value`, `type_text`, `select_option` (radio / pop-up), `menu_pick`, `scroll`, `activate`, `open_url` (always a new Safari window), `navigate` (only our window). Each one re-observes, checks the expected change and returns `VERIFIED`, `UNVERIFIED` or `REFUSED`. |
| `driver.py` | `read_web(snapshot)` turns the AX web area into controls, forms, live regions, tables and text. `MacBrowserBackend` implements micro_agent's browser interface (`open/click/fill/select/read_raw/.page/.log`, returning `web_tools.Page`). `MacEnv` implements the teach-once env interface (`page/open/switch/click/type/is_web/secret`; file operations stay in TestbedEnv). `micro_browser(factory)` swaps the backend into `run_dev_dashboard` for one block. |
| `probe.py` | `probe`, `demo --target dd|teach_t3`, `vm-run`, `preflight`, and loading `live.env`. |

### Action rules (enforced in code)

1. **Gates**, checked in this order:
   - guest VM;
   - app allowlist (default Safari and TextEdit only);
   - the existing `app_policy.AppPolicy` tier for the action kind (terminals, model clients and security UI stay forbidden even if allow-listed);
   - element enabled;
   - **destructive gate**: if `ax_compress.button_role(node) == "destructive"`, the `approve(request)` callback must return True. With no callback the action is denied. This applies to presses, menu items and pop-up items.
2. **Least invasive method first.**
   - Clicks: `AXPress`, and only then a CGEvent click at the element's visible centre. The CGEvent click is refused if the point belongs to another app.
   - Text: `AXValue`, and only then focus plus keystrokes (CGEventKeyboardSetUnicodeString). Keystrokes are posted **to the target pid only** (`CGEventPostToPid`). Synthetic events are skipped when `CGPreflightPostEventAccess` is false.
3. **Verification.** The actor re-observes and checks the expected change: value equals; secure length equals (AXNumberOfCharacters); focus moved; URL equals or changed; an element appeared or disappeared; selection; front app; any change (`ax_compress.diff` plus the URL). If nothing changed the result is UNVERIFIED, never VERIFIED.
4. **Secrets.**
   - A secure field only accepts exactly one `{{secret:name}}` handle. The handle is resolved through the broker (`SecretBroker.substitute`, which keeps bindings and records use) when the value is typed, or the field takes a `Revealed` value that a trusted sink already resolved (micro_agent's `fill` passes plaintext, so the backend wraps password fields in `Revealed`).
   - Plain text into a secure field is REFUSED.
   - Results and logs show the handle, `<secret>` or `<N chars>`, never the value.
5. **URLs.**
   - Only `http(s)://127.0.0.1|localhost|::1` and `file://` are allowed, unless an entry in `allow_urls` explicitly allows more. MacBrowserBackend additionally applies `web_tools.check_loopback`.
   - Safari pages open in a **new window**: File > New Window through AX, with Cmd-N as fallback. When Safari is not running, `open -a Safari <url>` creates the first window.
   - An existing window or tab is never navigated. If no new window can be made, the action is refused.
   - Navigation within our window goes through its address field: AXValue, then AXConfirm, falling back to Return posted to Safari's pid. Before typing, the actor checks that our window is the key window.

### AX reading assumptions (to confirm on real Safari)

These shapes come from what WebKit is documented or known to expose. They are implemented in
the fake used by the tests and are the most likely place for first-run failures:

- Links are AXLink (with AXURL), buttons and `<a role=button>` are AXButton, text inputs are AXTextField, and password inputs are AXTextField with subrole AXSecureTextField.
- A labelled input is named via AXTitle, AXDescription or AXTitleUIElement.
- `fieldset role=radiogroup` is AXRadioGroup and contains AXRadioButtons named by their labels.
- `<form>` has subrole AXLandmarkForm or role description "form". If neither shows up, a button belongs to the innermost ancestor that contains an input (the web area counts) or to its dialog. Every form is treated as POST, so micro_agent asks for approval before the first submit (conservative).
- Live regions use subroles AXApplicationAlert, AXApplicationStatus, AXLandmarkRegion and AXApplicationAlertDialog.
- Tables are AXTable > AXRow > AXCell, and the first row is treated as the header.
- `<code id|aria-label>` holding a credential is a labelled group whose only content is static text. It becomes `secret_text`, readable only through `read_raw`.
- The HTTP status is not in the AX tree. A page titled "Not found" or "404" is reported as 404.
- Safari's address field matches `/address|url|WEB_BROWSER/` in its identifier, title or description.

## 3. Demos (scripted, no model calls)

- `demo --target dd [--variant a|b|c] [--seed 7]`:
  1. Seeds the dev_dashboard testbed.
  2. Serves it on 127.0.0.1.
  3. Runs the unchanged `micro_agent.run_dev_dashboard` with the scripted model (the same steps as `tests/test_micro_agent.py`) through `MacBrowserBackend` in a new Safari window. Real WP6 grounding, the WP7 broker, the effects sink and the create-key ledger all apply.
  4. Verifies with `testbeds/dev_dashboard/verify.py`.
- `demo --target teach_t3 [--instance i03]`:
  1. Records the scripted demonstration with the stdlib browser (no UI).
  2. Induces the skill.
  3. Runs it on held-out instance i03 through `MacEnv`, using broker secrets (handles substituted at typing time).
  4. Verifies with `testbeds/teach_once/verify.py`. i03 passes headless with the v1 skill, so the result isolates the driver.
- Results are written to `~/.awos/live_results/<target>-<timestamp>.json`. Exit codes: 0 PASS, 1 verify FAIL, 2 refused (host / permissions), 3 error. The demo closes only its own windows.

## 4. Owner-side commands

One-time guest setup (in the guest, as `admin`):

```bash
# after the first vm-run has synced the repo to ~/AWOS_coding_agent
cd ~/AWOS_coding_agent
python3 -m venv .venv && .venv/bin/pip install pyobjc-framework-ApplicationServices \
    pyobjc-framework-Quartz pyobjc-framework-Cocoa keyring pytest
AWOS_HELPER_PYTHON=$HOME/AWOS_coding_agent/.venv/bin/python scripts/macos_helper/build_helper.sh
# System Settings > Privacy & Security > Accessibility: enable "AWOS Helper" (guest only)
```

From the host:

```bash
python -m scaffold.agent.desktop.live vm-run --vm <name> -- scaffold.agent.desktop.live probe
python -m scaffold.agent.desktop.live vm-run --vm <name> -- scaffold.agent.desktop.live demo --target dd
python -m scaffold.agent.desktop.live vm-run --vm <name> -- scaffold.agent.desktop.live demo --target teach_t3
#   --build-helper   rebuild the helper in the guest first (a rebuild may require re-granting)
#   --dry-run        print the rsync/ssh commands only
#   --ip IP          skip `tart ip`;  --user admin;  --pull DIR (default ./live_results)
```

What `vm-run` does, in order:

1. `rsync -az --delete` the repo to `admin@<ip>:AWOS_coding_agent/`. It excludes `.git`, `.venv*`, `.awos`, `.claude` and caches; the guest's own `.venv` is protected.
2. Writes `~/.awos/helper/live.env` in the guest with `AWOS_LOCAL_BASE_URL` and `AWOS_LIVE_GUEST=1`. This is needed because `open(1)` does not pass the ssh environment to the helper. `__main__` loads only `AWOS_*` keys from it.
3. Optionally rebuilds the helper.
4. Runs `ssh ... 'cd AWOS_coding_agent && scripts/macos_helper/run_helper.sh <command>'`. The helper's log is printed and its exit code returned.
5. Rsyncs `~/.awos/live_results/` back.

### Model URL for guest runs

- **Bridge (default).** `AWOS_LOCAL_BASE_URL=http://<host bridge>:8080/v1`, where the bridge is the guest IP's `.1`, e.g. `192.168.64.1`. Override with `--model-url`. llama-server on the host must listen on the bridge interface. **Caveat:** `micro_agent.LocalGBNFModel` currently calls `web_tools.check_loopback` on its base URL, so it rejects a bridge IP. That needs a small change in micro_agent, such as an allowed-hosts setting. It was left alone here because micro_agent.py was outside this change's files.
- **Tunnel (works today).** `vm-run --tunnel 8080` adds `ssh -R8080:127.0.0.1:8080`, so the host's llama-server shows up as `127.0.0.1:8080` inside the guest. `AWOS_LOCAL_BASE_URL` then becomes `http://127.0.0.1:8080/v1`, which passes the loopback check. The tunnel only lives for the duration of that ssh command.

## 5. Tests

`tests/test_live_driver.py`:

- **FakeRuntime and FakeSafari.** FakeSafari renders the testbeds' real 127.0.0.1 HTML into an AX element tree. It provides File > New Window, an address field with AXConfirm, a close button, AXPress, AXValue (secure fields store the value hidden and expose only AXNumberOfCharacters), radio groups, forms that really POST with cookies, tables, live regions and dialogs.
- **Covered:**
  - observe: tree shape, secure value omitted, budget and timeout truncation, grounding refusing a truncated tree, AXTitleUIElement labels, link names from content;
  - actions: verified / unverified / disabled, the destructive gate, the VM, allowlist and app-policy gates, secret handle and Revealed rules, no keystrokes without event access, URL allowlist, new window leaving the owner's window untouched, `open -a` when Safari is not running, menu pick, activate;
  - the backend reading and acting on a page;
  - **both demos end to end**: dd variants a and b, and teach_t3 i03 all PASS the testbed verifiers, close only their own window and never leak the key or password into the log;
  - probe refusing sensitive apps and the host;
  - the vm-run command shape and `tart ip`;
  - `live.env` filtering.
- **Real-UI tests** are marked and skipped unless `AWOS_LIVE_UI=1`, the process is in the guest VM and Accessibility is granted. None run on the host.

## 6. Risks / open items

- **Real WebKit AX shapes may differ from the fake** (section 2). The most likely problems:
  - form detection, which falls back to the conservative synthetic-form rule;
  - the AXTitleUIElement label text;
  - setting AXValue on Safari's address field and on web text fields (fallback: keystrokes to the pid);
  - AXConfirm on the address field (fallback: Return).
  Expect one iteration after the first guest run. `probe` prints the compressed tree to compare against.
- **Page settle** means "two equal consecutive reads". A page still mutating via JavaScript could settle early; the testbed pages are static.
- **Downloads** are not supported in MacEnv (teach t1/t2). Only t3 is wired.
- **`run_helper.sh` splits arguments on spaces**, so demo arguments must not contain spaces.
- **Rebuilding the helper changes its ad-hoc signature**, so macOS may ask the owner to re-grant.
- **Model URL**: see the `check_loopback` caveat in section 4.
