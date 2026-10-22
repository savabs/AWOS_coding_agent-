"""Factory families: every module in this package registers its Family classes on import."""
import importlib
import pkgutil

for _m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    if not _m.name.startswith("_"):
        importlib.import_module(f"{__name__}.{_m.name}")
