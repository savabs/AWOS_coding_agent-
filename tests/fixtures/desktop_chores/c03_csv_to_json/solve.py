"""Reference solution for c03_csv_to_json (scripted, rung: shell). Runs with cwd = scratch."""
import csv, json
with open("contacts.csv", newline="") as f:
    rows = [dict(r, balance=float(r["balance"])) for r in csv.DictReader(f)]
with open("contacts.json", "w") as f:
    json.dump(rows, f, indent=2)
