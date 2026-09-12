"""Exceptions raised by app/collector/*. Caught by runner.py to classify a
CollectorRun's terminal status and decide whether to fire a WeCom alert.
"""


class CollectorError(Exception):
    """Base class for all collector failures."""


class SessionExpiredError(CollectorError):
    """The saved storage_state no longer logs the browser in.

    Raised when, after navigating to the portal's data page, the page looks
    like a login/QR screen instead. Recovery requires a human to re-run
    `bootstrap-login` locally and re-upload the session file.
    """


class WrongAccountError(CollectorError):
    """The saved session is live and logged in, but resolves to the wrong
    sub-account/professional account rather than the one this target is
    for (e.g. a personal XHS account instead of the linked business
    account). Found live: this state passes every "does this look like a
    login page" check -- same URL, same not-a-login-page signal -- because
    it isn't one; only the page body differs.

    Recovery is different from SessionExpiredError: re-running
    bootstrap-login and re-uploading the *same* selection just reproduces
    the bug. The human must re-run bootstrap-login and explicitly pick the
    correct sub-account this time.
    """


class DownloadTimeoutError(CollectorError):
    """The export button was clicked (or the page was ready) but no download
    landed within collector_download_timeout_seconds."""


class EmptyExportError(CollectorError):
    """The upload API accepted the request but parsed zero data rows out of
    the exported file -- surfaced by the server as a 400 with a stable
    "文件中未解析到有效行" message (see app/views/media/*.py). Deliberately
    kept distinct from UploadFailedError: a 400 from bad/rejected data and a
    400 because there was nothing to export (or the session silently points
    at the wrong account and every export from it is empty) call for
    different operator responses, so they shouldn't share one alert.
    """


class UploadFailedError(CollectorError):
    """The downloaded file was posted to the existing upload API but the API
    rejected it or was unreachable."""


class XhsApiError(CollectorError):
    """One of XHS's "数据概览" JSON APIs (collect_xhs_overview, app/collector/
    xhs.py) either never returned parseable JSON within the capture budget,
    or answered with a valid HTTP response carrying a non-zero business
    `code` — a live, correctly-authenticated session that the endpoint is
    nonetheless refusing (e.g. permissions changed mid-session). Deliberately
    not SessionExpiredError: re-running bootstrap-login is not known to fix
    this and the operator needs to see the raw API error to tell the two
    apart.
    """
