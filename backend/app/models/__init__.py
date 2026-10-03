"""Auto-import every module in this package so Base.metadata sees all tables.
Add new model files here; no edits to this __init__ needed."""
import importlib
import pkgutil

for _m in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_m.name}")
