"""Reference solution for c09_defaults_write (scripted, rung: applescript). Runs with cwd = scratch."""
import os, subprocess
domain = os.path.join(os.getcwd(), "Prefs", "com.example.editor")
subprocess.run(["defaults", "write", domain, "AutoSaveInterval", "-int", "30"], check=True)
