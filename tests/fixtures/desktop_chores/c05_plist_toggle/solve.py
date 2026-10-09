"""Reference solution for c05_plist_toggle (scripted, rung: shell). Runs with cwd = scratch."""
import plistlib
p = "Preferences/com.example.viewer.plist"
with open(p, "rb") as f:
    d = plistlib.load(f)
d["ShowHiddenFiles"] = True
d["RecentLimit"] = 25
with open(p, "wb") as f:
    plistlib.dump(d, f)
