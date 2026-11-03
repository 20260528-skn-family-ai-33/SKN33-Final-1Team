"""Compatibility entrypoint; implementation: backend.app.ocr.adaptive."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("backend.app.ocr.adaptive", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("backend.app.ocr.adaptive")
