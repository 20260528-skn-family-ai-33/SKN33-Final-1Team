"""Compatibility entrypoint; implementation: backend.app.ocr.paddle_condition_worker."""
if __name__ == "__main__":
    import runpy
    runpy.run_module("backend.app.ocr.paddle_condition_worker", run_name="__main__")
else:
    import importlib
    import sys
    sys.modules[__name__] = importlib.import_module("backend.app.ocr.paddle_condition_worker")
