"""Reference solution for c04_ini_port (scripted, rung: shell). Runs with cwd = scratch."""
import configparser
cp = configparser.ConfigParser(interpolation=None)
cp.read("config/settings.ini")
cp["server"]["port"] = "9090"
with open("config/settings.ini", "w") as f:
    cp.write(f)
