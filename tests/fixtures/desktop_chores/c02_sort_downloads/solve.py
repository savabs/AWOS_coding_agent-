"""Reference solution for c02_sort_downloads (scripted, rung: shell). Runs with cwd = scratch."""
import os, shutil
for name in os.listdir("Downloads"):
    src = os.path.join("Downloads", name)
    if not os.path.isfile(src):
        continue
    dest = {"pdf": "PDFs", "zip": "Archives"}.get(name.rsplit(".", 1)[-1].lower())
    if dest:
        os.makedirs(os.path.join("Downloads", dest), exist_ok=True)
        shutil.move(src, os.path.join("Downloads", dest, name))
