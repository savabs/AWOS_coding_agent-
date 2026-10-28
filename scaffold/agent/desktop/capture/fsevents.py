"""
fsevents.py — FSEvents file watcher (written for AWOS; nothing vendored).

    w = FSEventsWatcher(["/path/home"], callback)   # callback(record: dict)
    w.schedule(run_loop)                            # on the capture thread's CFRunLoop
    ...
    w.close()

Each callback record is a native "fs" event (see adapter.py):

    {"src": "fs", "path": "/abs/path", "flags": <raw FSEvents bitmask>, "inode": 1234 | None, "t": ...}

The raw bitmask is kept on purpose: FSEvents coalesces flags (one record can
say Created|Modified|Renamed), so the adapter normalises them against the
file system and its own path->inode map instead of trusting single bits.
decode_flags() gives the names. PyObjC (FSEvents) is imported only in
FSEventsWatcher.__init__, so this module imports anywhere.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional

# FSEvents.h, kFSEventStreamEventFlag*
EVENT_FLAGS = {
    0x00000001: "MustScanSubDirs",
    0x00000002: "UserDropped",
    0x00000004: "KernelDropped",
    0x00000008: "EventIdsWrapped",
    0x00000010: "HistoryDone",
    0x00000020: "RootChanged",
    0x00000040: "Mount",
    0x00000080: "Unmount",
    0x00000100: "ItemCreated",
    0x00000200: "ItemRemoved",
    0x00000400: "ItemInodeMetaMod",
    0x00000800: "ItemRenamed",
    0x00001000: "ItemModified",
    0x00002000: "ItemFinderInfoMod",
    0x00004000: "ItemChangeOwner",
    0x00008000: "ItemXattrMod",
    0x00010000: "ItemIsFile",
    0x00020000: "ItemIsDir",
    0x00040000: "ItemIsSymlink",
    0x00080000: "OwnEvent",
    0x00100000: "ItemIsHardlink",
    0x00200000: "ItemIsLastHardlink",
    0x00400000: "ItemCloned",
}
FLAG_BITS = {v: k for k, v in EVENT_FLAGS.items()}

# kFSEventStreamCreateFlag*
CREATE_USE_CF_TYPES = 0x01
CREATE_NO_DEFER = 0x02
CREATE_IGNORE_SELF = 0x08
CREATE_FILE_EVENTS = 0x10
CREATE_USE_EXTENDED_DATA = 0x40
SINCE_NOW = 0xFFFFFFFFFFFFFFFF          # kFSEventStreamEventIdSinceNow


def decode_flags(mask: int) -> List[str]:
    return [name for bit, name in EVENT_FLAGS.items() if mask & bit]


def encode_flags(names) -> int:
    m = 0
    for n in names:
        m |= FLAG_BITS[n]
    return m


class FSEventsWatcher:
    def __init__(self, paths: List[str], callback: Callable[[dict], None], *, latency: float = 0.05,
                 frameworks: Optional[Dict[str, Any]] = None):
        if frameworks is None:
            from .permissions import load_frameworks
            frameworks = load_frameworks(("FSEvents",))
        self.fs = frameworks["FSEvents"]
        self.cf = frameworks.get("Quartz") or self.fs      # CFRunLoop constants live in the CF bridge
        self.paths = [os.path.realpath(p) for p in paths]
        self.callback = callback
        self.latency = latency
        self._stream = None
        self._cb_ref = self._on_events          # keep the bound method alive for the C callback

    def _on_events(self, stream, info, n, paths, flags, ids) -> None:
        for i in range(int(n)):
            p = paths[i]
            inode = None
            if isinstance(p, dict) or hasattr(p, "objectForKey_"):     # extended data
                inode = p.get("fileID") if hasattr(p, "get") else p.objectForKey_("fileID")
                p = p.get("path") if hasattr(p, "get") else p.objectForKey_("path")
            mask = int(flags[i])
            if mask & (FLAG_BITS["HistoryDone"] | FLAG_BITS["Mount"] | FLAG_BITS["Unmount"]):
                continue
            if inode is None:
                try:
                    inode = os.lstat(str(p)).st_ino
                except OSError:
                    inode = None
            self.callback({"src": "fs", "path": str(p), "flags": mask,
                           "inode": int(inode) if inode is not None else None, "t": time.time()})

    def schedule(self, run_loop: Any) -> None:
        fs = self.fs
        flags = CREATE_USE_CF_TYPES | CREATE_NO_DEFER | CREATE_FILE_EVENTS | CREATE_USE_EXTENDED_DATA
        self._stream = fs.FSEventStreamCreate(None, self._cb_ref, None, self.paths, SINCE_NOW,
                                              self.latency, flags)
        if self._stream is None:
            raise RuntimeError(f"FSEventStreamCreate failed for {self.paths}")
        fs.FSEventStreamScheduleWithRunLoop(self._stream, run_loop, self.cf.kCFRunLoopDefaultMode)
        if not fs.FSEventStreamStart(self._stream):
            raise RuntimeError("FSEventStreamStart failed")

    def close(self) -> None:
        if self._stream is not None:
            fs = self.fs
            fs.FSEventStreamStop(self._stream)
            fs.FSEventStreamInvalidate(self._stream)
            # PyObjC owns the stream reference; dropping it releases it (no explicit Release).
            self._stream = None
