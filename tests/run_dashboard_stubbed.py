"""AppTest entry point for the whole dashboard shell, backed by the stub API.

tests/run_dashboard.py imports the real dashboard against a dead API_URL, which
is enough for the login view but not for anything behind auth: every page call
raises and the sidebar cannot be exercised. This installs StubClient so
navigation, the sidebar and routing can be tested end to end.

Two things keep this from leaking into other tests in the same process:

* app.ui.dashboard is dropped from sys.modules before the import and again
  after. AppTest re-executes this file on every rerun, but importing an
  already-imported module is a no-op, so without the first pop the dashboard
  body would run only once and every rerun would render an empty app; without
  the second, tests/run_dashboard.py would import nothing and render nothing.
* APIClient is restored immediately afterwards, so tests that patch or
  exercise the real client still see the real class.
"""
import sys

import app.api_client
from tests.page_registry import StubClient

_real_client = app.api_client.APIClient
app.api_client.APIClient = StubClient
sys.modules.pop("app.ui.dashboard", None)
try:
    import app.ui.dashboard  # noqa: F401 — executing the module *is* the render
finally:
    app.api_client.APIClient = _real_client
    sys.modules.pop("app.ui.dashboard", None)
