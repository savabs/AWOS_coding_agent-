"""Reference solution for c08_sqlite_done (scripted, rung: shell). Runs with cwd = scratch."""
import sqlite3
con = sqlite3.connect("data/tasks.db")
con.execute("UPDATE tasks SET done = 1 WHERE title = ?", ("File taxes",))
con.commit()
con.close()
