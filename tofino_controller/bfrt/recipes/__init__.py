# Optional convenience utilities for discovering recipe modules.
# You can ignore this if you prefer explicit imports from recipes.
import pkgutil, importlib

def load_modules():
    mods = {}
    pkg = __name__
    for m in pkgutil.iter_modules(__path__):
        if m.ispkg:
            continue
        mods[m.name] = importlib.import_module(f"{pkg}.{m.name}")
    return mods
