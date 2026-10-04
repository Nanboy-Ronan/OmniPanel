"""Page registry and stub API client shared by the render tests.

Kept apart from tests/run_page.py so importing the registry does not execute
the Streamlit script body.
"""

from app.ui.registry import ADMIN_PAGES, ECOMMERCE_PAGES, MEDIA_PAGES

#: Every page the dashboard can route to, regardless of role. Imported from the
#: one registry rather than restated here, so the two cannot drift.
PAGES = {**ECOMMERCE_PAGES, **MEDIA_PAGES, **ADMIN_PAGES}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.headers = {}

    def json(self):
        return self._payload

    @property
    def text(self):
        return str(self._payload)

    @property
    def content(self):
        return str(self._payload).encode()

    def iter_content(self, chunk_size=1):
        yield self.content


class StubClient:
    """Answers any APIClient method with an empty, well-formed 200.

    An empty dict iterates, sizes and truthiness-tests exactly like an empty
    list, so it stands in for both collection and object endpoints. Pages that
    need a particular shape get it through `payloads`.
    """

    def __init__(self, base_url=None, token=None, payloads=None, status=200):
        # Mirrors APIClient's signature so it can be swapped in wholesale.
        self._payloads = payloads or {}
        self._status = status
        self.base_url = base_url or "http://stub"
        self.token = token or "stub-token"

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            return FakeResponse(self._payloads.get(name, {}), self._status)

        return call

    def close(self):
        pass
