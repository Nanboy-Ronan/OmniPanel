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


class DownloadTimeoutError(CollectorError):
    """The export button was clicked (or the page was ready) but no download
    landed within collector_download_timeout_seconds."""


class UploadFailedError(CollectorError):
    """The downloaded file was posted to the existing upload API but the API
    rejected it or was unreachable."""
