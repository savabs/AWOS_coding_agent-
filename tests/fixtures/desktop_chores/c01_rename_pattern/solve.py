"""Reference solution for c01_rename_pattern (scripted, rung: shell). Runs with cwd = scratch."""
import os, re
for name in os.listdir("shots"):
    m = re.fullmatch(r"Screen Shot (\d{4}-\d{2}-\d{2}) at (\d{2})\.(\d{2})\.(\d{2})\.png", name)
    if m:
        d, h, mi, s = m.groups()
        os.rename(os.path.join("shots", name), os.path.join("shots", f"shot_{d}_{h}-{mi}-{s}.png"))
