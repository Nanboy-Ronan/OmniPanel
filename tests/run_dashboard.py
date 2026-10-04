"""AppTest entry point for the real dashboard against the configured API.

The module is dropped from sys.modules first: AppTest re-executes this file on
every rerun, and importing an already-imported module is a no-op, so otherwise
only the first run would render anything. It is dropped again afterwards so a
later run — here or in tests/run_dashboard_stubbed.py — starts clean.
"""
import sys

sys.modules.pop("app.ui.dashboard", None)
try:
    import app.ui.dashboard  # noqa: F401 — executing the module *is* the render
finally:
    sys.modules.pop("app.ui.dashboard", None)
