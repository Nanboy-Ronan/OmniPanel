from __future__ import annotations
import os
import requests
import urllib3

API_URL = os.getenv("API_URL", "http://localhost:8000")
API_VERIFY = os.getenv("API_VERIFY", "true").lower() not in ("0", "false", "no")
DEFAULT_TIMEOUT = int(os.getenv("API_TIMEOUT", "15"))

if not API_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class APIClient:
    """HTTP client for collector login, account discovery, and uploads.

    The React console owns interactive dashboard API calls in frontend/src/lib.
    Keep this client scoped to the collector rather than mirroring every route.
    """

    def __init__(self, base_url: str = API_URL, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._verify = API_VERIFY
        self._session = requests.Session()
        self._session.verify = self._verify
        self._session.headers.update({"Connection": "keep-alive"})

    def close(self) -> None:
        self._session.close()

    def _headers(self) -> dict:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _timeout(self, extra: int = 0) -> float:
        return DEFAULT_TIMEOUT + extra

    def login(self, email: str, password: str) -> requests.Response:
        r = self._session.post(
            f"{self.base_url}/auth/jwt/login",
            data={"username": email, "password": password},
            timeout=self._timeout(),
        )
        if r.status_code == 200:
            self.token = r.json().get("access_token")
        return r

    def upload(
        self,
        name: str,
        data: bytes,
        expected_platform: str | None = None,
    ) -> requests.Response:
        params: dict = {}
        if expected_platform:
            params["expected_platform"] = expected_platform
        return self._session.post(
            f"{self.base_url}/upload/",
            files={"file": (name, data)},
            params=params,
            headers=self._headers(),
            timeout=self._timeout(30),
        )

    def upload_batch(self, batch_id: int) -> requests.Response:
        """Return status and result for a specific upload batch."""
        return self._session.get(
            f"{self.base_url}/upload/batches/{batch_id}",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def upload_jd(self, file_bytes: bytes, filename: str) -> requests.Response:
        """Upload a JD collector CSV through the existing ecommerce ETL."""
        return self.upload(filename, file_bytes, expected_platform="jd")

    def xhs_accounts(self) -> requests.Response:
        return self._session.get(
            f"{self.base_url}/media/xhs/accounts",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def upload_xhs(self, file_bytes: bytes, filename: str, account_id: int) -> requests.Response:
        """Upload a Xiaohongshu xlsx export for upsert into xhs_posts."""
        return self._session.post(
            f"{self.base_url}/media/xhs/upload",
            data={"account_id": account_id},
            files={"file": (filename, file_bytes, "application/octet-stream")},
            headers={k: v for k, v in self._headers().items() if k != "Content-Type"},
            timeout=self._timeout(60),
        )

    def upload_xhs_overview(self, file_bytes: bytes, filename: str, account_id: int) -> requests.Response:
        """Upload a collect_xhs_overview() JSON payload for upsert into
        xhs_account_daily_metrics / xhs_audience_source_daily."""
        return self._session.post(
            f"{self.base_url}/media/xhs/upload_overview",
            data={"account_id": account_id},
            files={"file": (filename, file_bytes, "application/json")},
            headers={k: v for k, v in self._headers().items() if k != "Content-Type"},
            timeout=self._timeout(60),
        )

    def upload_pgy(self, file_bytes: bytes, filename: str, account_id: int) -> requests.Response:
        """Upload a Pugongying xlsx export for upsert into pgy_notes."""
        return self._session.post(
            f"{self.base_url}/media/pgy/upload",
            data={"account_id": account_id},
            files={"file": (filename, file_bytes, "application/octet-stream")},
            headers={k: v for k, v in self._headers().items() if k != "Content-Type"},
            timeout=self._timeout(60),
        )

    def wx_channels_accounts(self) -> requests.Response:
        return self._session.get(
            f"{self.base_url}/media/channels/accounts",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def upload_channels(self, file_bytes: bytes, filename: str, account_id: int) -> requests.Response:
        """Upload a 视频号助手 xlsx/csv export for upsert into wx_channels_posts."""
        return self._session.post(
            f"{self.base_url}/media/channels/upload",
            data={"account_id": account_id},
            files={"file": (filename, file_bytes, "application/octet-stream")},
            headers={k: v for k, v in self._headers().items() if k != "Content-Type"},
            timeout=self._timeout(60),
        )

    def upload_zhihu(self, file_bytes: bytes, filename: str, content_type: str) -> requests.Response:
        return self._session.post(
            f"{self.base_url}/media/zhihu/upload",
            data={"content_type": content_type},
            files={"file": (filename, file_bytes, "application/octet-stream")},
            headers={k: v for k, v in self._headers().items() if k != "Content-Type"},
            timeout=self._timeout(60),
        )
