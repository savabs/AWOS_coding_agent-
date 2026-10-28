"""Reference solution for c10_log_errors (scripted, rung: shell). Runs with cwd = scratch."""
import os
os.makedirs("reports", exist_ok=True)
with open("logs/app.log") as src, open("reports/errors.txt", "w") as dst:
    for line in src:
        if " ERROR " in line:
            dst.write(line)
