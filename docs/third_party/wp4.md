# WP4 native recorder: third-party code

Package: `scaffold/agent/desktop/capture/` (native macOS demonstration recorder,
feeding `testbeds/teach_once/trace.Recorder`, schema `awos.demo_trace/1`).

## Source and licence

- **openadapt-capture**, `packages/capture/` of the OpenAdapt monorepo (local clone at
  `~/Projects/REA/product2/_src/openadapt/`). Licence: **MIT**, "Copyright (c) 2025-2026
  OpenAdapt.AI". The full licence text is in `scaffold/agent/desktop/capture/NOTICE`. Each
  adapted file starts with an `SPDX-License-Identifier: MIT` header that names the source
  file and lists the AWOS changes.
- **Trademark.** "OpenAdapt" is a trademark of its owners (TRADEMARKS.md in that repo). The
  MIT licence covers the code only. No AWOS module, class, CLI or package is named after
  it, and the only mentions are attributions like this one.
- **Not used:** `legacy/openadapt/window/_macos.py`, which imports `oa_atomacos` and so has
  GPL lineage. Nothing from `legacy/` was read for code. We also did not use
  `openadapt_capture/window/_macos.py`, `recorder.py`, `capture.py`, `db/`, `storage*`,
  `video.py` or `window_capture*.py`.

## Import-closure check (BLUEPRINT critic item 10)

The package's `pyproject.toml` declares pydantic, pydantic-settings, SQLAlchemy, loguru,
mss, Pillow, psutil, numpy, tqdm, fire and PyObjC (ApplicationServices + ScreenCaptureKit).
`openadapt_capture/__init__.py` eagerly imports recorder, db, video, capture and
window_capture, so importing the package as a whole pulls in all of those.

Here is the closure of the three files WP4 needed, each traced on its own:

| file | imports |
|---|---|
| `structural.py` | stdlib + **pydantic** (the platform observers are imported lazily) |
| `structural_observer/macos.py` | `structural.py` + PyObjC `AppKit`, `ApplicationServices` (lazily, in `_AXRuntime.__init__`) + **psutil** (lazily, for the process name) |
| `input_observer/darwin.py` | `input_observer/base.py` (stdlib only: queue/threading/abc/dataclasses) + PyObjC `Quartz`, `ApplicationServices` (lazily) |

None of these files reaches SQLAlchemy, loguru, pydantic-settings, mss or ScreenCaptureKit.
Those come only through the package `__init__` and the recording pipeline. FSEvents does
not appear anywhere in openadapt-capture, so the watcher was written from scratch.

Result: we vendored by adapting the source, not by copying whole files. That removed
pydantic and psutil and kept the import closure to stdlib plus lazily imported PyObjC.

## File by file

| AWOS file | origin | what was taken / changed |
|---|---|---|
| `capture/schema.py` | adapted from `structural.py` | `omit_tree_value` and `_SECRET_VALUE_ROLE_KEYS` taken **verbatim**. The limits `MAX_*` come from the same file. The pydantic `StructuralElement/Ancestor/TreeNode` models were rewritten as plain dicts with `sanitize_element()` (known keys, capped text, no value on a secure field), and `ordinal` was added. |
| `capture/ax_macos.py` | adapted from `structural_observer/macos.py` | Ported the `_AXRuntime` PyObjC adapter (attribute, element at point, focused element with frontmost-app fallback, pid), the element-field reading (role, subrole, AXTitle/AXDescription name, AXIdentifier), the ancestry walk, and the bounded window-tree dump (`_as_tree_node`, which omits secure values). **Added:** `AXUIElementSetMessagingTimeout` on the system-wide element and every app element; `PermissionMissing`; NSRunningApplication in place of psutil; web-area URL, same-role ordinal, page text, tables, alerts and status; AXDocument; AXDefaultButton; AXObserver plumbing; AXManualAccessibility for Chromium. |
| `capture/input_tap.py` | adapted from `input_observer/darwin.py` + `base.py` | Ported a subset of the keycode table, the permission-preflight approach, tap-disabled recovery, and `_is_injected` (source PID > 0). **Changed:** `kCGEventTapOptionListenOnly` replaces the active-filter `kCGEventTapOptionDefault`. Permissions are never requested from here. The delivery-barrier and receipt machinery and the 874-line threaded base class were dropped. Character keys are never decoded (no `CGEventKeyboardGetUnicodeString`). |
| `capture/fsevents.py` | AWOS original | FSEvents stream: FileEvents, UseCFTypes, NoDefer and UseExtendedData (for the inode), plus flag decoding. |
| `capture/adapter.py` | AWOS original | Converts native records into trace.Recorder events. It applies the privacy rules and the app/web rules, and normalises FSEvents output (rename pairing, atomic saves, "untitled folder", temp files, downloads). |
| `capture/live.py` | AWOS original | Runs one CFRunLoop holding the tap, the AX observers, FSEvents and the focused-application poll. |
| `capture/permissions.py` | AWOS original | Loads PyObjC lazily and preflights TCC, with no prompt unless asked. Its remediation wording follows openadapt-capture's "allow the app that runs the recorder" hint. |
| `capture/__main__.py` | AWOS original | CLI with `check`, `record` (live, or `--from-events` replay) and `validate`. |

Design input that is not code: the recorder reports for playwright-codegen (Apache-2.0)
and selenium-ide (Apache-2.0). From them we took the rules that fill means type,
not click-into-field; that a navigation attaches to the action that caused it; and that
password values must never be recorded, because Selenium IDE records them in plain text.
No code was copied from either project.

## Runtime dependencies (optional, macOS only)

`pip install pyobjc-framework-ApplicationServices pyobjc-framework-Quartz pyobjc-framework-Cocoa pyobjc-framework-FSEvents`
installs PyObjC, which is MIT (the libffi it bundles is MIT too). These packages are not
required to import or test the package.
