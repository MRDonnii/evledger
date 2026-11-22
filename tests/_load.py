"""Load the integration's pure modules without importing Home Assistant."""

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).parents[1] / "custom_components" / "evledger" / "smart"


def load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
