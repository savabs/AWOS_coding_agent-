"""
scaffold.agent.desktop.capture — native macOS demonstration recorder (WP4).

Records a person doing a task once (clicks, typing into fields, page loads,
documents opened, files moved/created) as the semantic teach_once trace
(testbeds/teach_once/trace.py, schema awos.demo_trace/1).

    adapter.py      native records -> trace.Recorder events (privacy rules, FS normalisation)
    live.py         MacRecorder: one CFRunLoop with the tap, AX observers, FSEvents, app poll
    ax_macos.py     PyObjC AX reads (adapted, MIT) + AX messaging timeouts
    input_tap.py    listen-only CGEventTap (adapted, MIT); injected-event detection
    fsevents.py     FSEvents watcher
    schema.py       element schema + secure-value omission (adapted, MIT)
    permissions.py  lazy PyObjC loading, TCC preflight, clear errors
    __main__.py     CLI: check | record (live or --from-events) | validate

Importing this package never imports PyObjC and never touches TCC; only
opening a live recorder does. Third-party notices: ./NOTICE, docs/third_party/wp4.md.
"""
from .adapter import ConsentRequired, NativeAdapter, sanitize_record
from .permissions import CaptureUnavailable, PermissionMissing, PyObjCMissing, check_permissions
from .schema import EVENTS_SCHEMA, omit_tree_value, sanitize_element

__all__ = ["CaptureUnavailable", "ConsentRequired", "EVENTS_SCHEMA", "NativeAdapter", "PermissionMissing",
           "PyObjCMissing", "check_permissions", "omit_tree_value", "sanitize_element", "sanitize_record",
           "MacRecorder"]


def __getattr__(name):
    if name == "MacRecorder":
        from .live import MacRecorder
        return MacRecorder
    raise AttributeError(name)
