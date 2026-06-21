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
    """Small wrapper around HTTP calls to the RPA API."""

    def __init__(self, base_url: str = API_URL, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._verify = API_VERIFY
        self._session = requests.Session()
        self._session.verify = self._verify
        self._session.headers.update({"Connection": "keep-alive"})

    def _headers(self) -> dict:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _timeout(self, extra: int = 0) -> float:
        return DEFAULT_TIMEOUT + extra

    def register(self, email: str, password: str) -> requests.Response:
        return self._session.post(
            f"{self.base_url}/auth/register",
            json={"email": email, "password": password},
            timeout=self._timeout(),
        )

    def login(self, email: str, password: str) -> requests.Response:
        r = self._session.post(
            f"{self.base_url}/auth/jwt/login",
            data={"username": email, "password": password},
            timeout=self._timeout(),
        )
        if r.status_code == 200:
            self.token = r.json().get("access_token")
        return r

    def wecom_authorize_url(self, redirect_uri: str) -> requests.Response:
        """Return an Enterprise WeChat OAuth authorization URL."""
        return self._session.get(
            f"{self.base_url}/auth/wecom/authorize-url",
            params={"redirect_uri": redirect_uri},
            timeout=self._timeout(),
        )

    def wecom_exchange(self, code: str, state: str) -> requests.Response:
        """Exchange an Enterprise WeChat OAuth code for this app's JWT."""
        r = self._session.post(
            f"{self.base_url}/auth/wecom/exchange",
            json={"code": code, "state": state},
            timeout=self._timeout(),
        )
        if r.status_code == 200:
            self.token = r.json().get("access_token")
        return r

    def me(self) -> requests.Response:
        """Return the current user's identity, role, and display name."""
        return self._session.get(
            f"{self.base_url}/auth/me",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def upload(self, name: str, data: bytes) -> requests.Response:
        return self._session.post(
            f"{self.base_url}/upload/",
            files={"file": (name, data)},
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

    def upload_batch_rejected(self, batch_id: int) -> requests.Response:
        """Return rejected rows for a specific upload batch."""
        return self._session.get(
            f"{self.base_url}/upload/batches/{batch_id}/rejected",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def analysis(self, start_date: str, end_date: str, platform: str | None = None) -> requests.Response:
        params = {"start_date": start_date, "end_date": end_date}
        if platform:
            params["platform"] = platform
        return self._session.get(
            f"{self.base_url}/analysis/",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def overview(self, start_date: str, end_date: str, platform: str | None = None) -> requests.Response:
        """Call the `/analysis/overview` endpoint."""
        params = {"start_date": start_date, "end_date": end_date}
        if platform:
            params["platform"] = platform
        return self._session.get(
            f"{self.base_url}/analysis/overview",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def repurchase_rate(
        self,
        start_date: str,
        end_date: str,
        platform: str | None = None,
        window_days: int | None = None,
    ) -> requests.Response:
        """Return repurchase metrics for the selected date range."""
        params: dict = {"start_date": start_date, "end_date": end_date}
        if platform:
            params["platform"] = platform
        if window_days is not None:
            params["window_days"] = window_days
        return self._session.get(
            f"{self.base_url}/analysis/repurchase_rate",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def cohort_retention(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        platform: str | None = None,
        max_offset: int = 12,
    ) -> requests.Response:
        """Return monthly cohort retention (per-period + cumulative)."""
        params: dict = {"max_offset": max_offset}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if platform:
            params["platform"] = platform
        return self._session.get(
            f"{self.base_url}/analysis/cohort_retention",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def customers(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        min_orders: int | None = None,
        platform: str | None = None,
    ) -> requests.Response:
        """Return aggregated metrics for all customers."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if min_orders:
            params["min_orders"] = min_orders
        if platform:
            params["platform"] = platform
        return self._session.get(
            f"{self.base_url}/analysis/customers",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def customer_orders(
        self,
        customer_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> requests.Response:
        """Return order history for a single customer."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._session.get(
            f"{self.base_url}/analysis/customers/{customer_id}",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def identity_clusters(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        confidence: str | None = None,
    ) -> requests.Response:
        """Return cross-platform customer identity clusters (phone-based)."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if confidence:
            params["confidence"] = confidence
        return self._session.get(
            f"{self.base_url}/analysis/identity/clusters",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def orders_all(self) -> requests.Response:
        return self._session.get(
            f"{self.base_url}/orders_all/",
            headers=self._headers(),
            timeout=self._timeout(30),
        )

    def order_raw(self, order_id: int) -> requests.Response:
        return self._session.get(
            f"{self.base_url}/orders_all/{order_id}/raw",
            headers=self._headers(),
            timeout=self._timeout(30),
        )

    def sql_query(self, sql: str) -> requests.Response:
        return self._session.post(
            f"{self.base_url}/analysis/sql",
            json={"sql": sql},
            headers=self._headers(),
            timeout=self._timeout(15),
        )

    def nl_sql_providers(self) -> requests.Response:
        """中文问数据: list AI providers configured server-side and their models."""
        return self._session.get(
            f"{self.base_url}/analysis/nl-sql/providers",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def nl_sql_query(
        self,
        question: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> requests.Response:
        """中文问数据: send a natural-language question, get generated SQL + rows.

        ``provider``/``model`` are the user's dropdown choice; omit to use the
        server default. API keys never leave the server.
        """
        payload: dict = {"question": question}
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        return self._session.post(
            f"{self.base_url}/analysis/nl-sql",
            json=payload,
            headers=self._headers(),
            timeout=self._timeout(60),
        )

    def db_status(self) -> requests.Response:
        """Return database readiness details (admin only)."""
        return self._session.get(
            f"{self.base_url}/admin/db-status",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    # ─── User management ───────────────────────────────────────────────

    def list_users(self) -> requests.Response:
        """Return all user accounts (admin only)."""
        return self._session.get(
            f"{self.base_url}/admin/users",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def update_role(self, user_id: str, role: str) -> requests.Response:
        """Update another user's role (admin only)."""
        return self._session.put(
            f"{self.base_url}/admin/users/{user_id}/role",
            json={"role": role},
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def create_user(self, email: str, password: str, role: str) -> requests.Response:
        """Create a new user account (admin only)."""
        return self._session.post(
            f"{self.base_url}/admin/users",
            json={"email": email, "password": password, "role": role},
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def update_password(self, user_id: str, password: str) -> requests.Response:
        """Reset another user's password (admin only)."""
        return self._session.put(
            f"{self.base_url}/admin/users/{user_id}/password",
            json={"password": password},
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def update_active(self, user_id: str, is_active: bool) -> requests.Response:
        """Enable or disable a user account (admin only)."""
        return self._session.put(
            f"{self.base_url}/admin/users/{user_id}/active",
            json={"is_active": is_active},
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def list_logs(self, user_id: str | None = None) -> requests.Response:
        """Return operation logs (admin only)."""
        params = {"user_id": user_id} if user_id else {}
        return self._session.get(
            f"{self.base_url}/admin/logs",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def delete_user(self, user_id: str) -> requests.Response:
        """Delete a user account (admin only)."""
        return self._session.delete(
            f"{self.base_url}/admin/users/{user_id}",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def clear_db(self) -> requests.Response:
        """Clear all order data (admin only)."""
        return self._session.post(
            f"{self.base_url}/admin/clear-db",
            headers=self._headers(),
            timeout=self._timeout(30),
        )

    # ── Saved queries ─────────────────────────────────────────────────────────

    def list_saved_queries(self) -> requests.Response:
        return self._session.get(
            f"{self.base_url}/saved-queries/",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def save_query(self, name: str, filters_json: dict, is_shared: bool = False) -> requests.Response:
        return self._session.post(
            f"{self.base_url}/saved-queries/",
            json={"name": name, "filters_json": filters_json, "is_shared": is_shared},
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def delete_saved_query(self, query_id: str) -> requests.Response:
        return self._session.delete(
            f"{self.base_url}/saved-queries/{query_id}",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    # ── XHS accounts ─────────────────────────────────────────────────────────

    def xhs_accounts(self) -> requests.Response:
        return self._session.get(
            f"{self.base_url}/media/xhs/accounts",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def create_xhs_account(self, name: str) -> requests.Response:
        return self._session.post(
            f"{self.base_url}/media/xhs/accounts",
            json={"name": name},
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def rename_xhs_account(self, account_id: int, name: str) -> requests.Response:
        return self._session.patch(
            f"{self.base_url}/media/xhs/accounts/{account_id}",
            json={"name": name},
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def delete_xhs_account(self, account_id: int) -> requests.Response:
        return self._session.delete(
            f"{self.base_url}/media/xhs/accounts/{account_id}",
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

    def xhs_posts(
        self,
        account_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 200,
    ) -> requests.Response:
        """Return XHS posts, optionally filtered by account and date range."""
        params: dict = {"limit": limit}
        if account_id is not None:
            params["account_id"] = account_id
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._session.get(
            f"{self.base_url}/media/xhs/posts",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    # ── Zhihu ─────────────────────────────────────────────────────────────────

    def upload_zhihu(self, file_bytes: bytes, filename: str, content_type: str) -> requests.Response:
        return self._session.post(
            f"{self.base_url}/media/zhihu/upload",
            data={"content_type": content_type},
            files={"file": (filename, file_bytes, "application/octet-stream")},
            headers={k: v for k, v in self._headers().items() if k != "Content-Type"},
            timeout=self._timeout(60),
        )

    def zhihu_posts(
        self,
        content_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 200,
    ) -> requests.Response:
        params: dict = {"limit": limit}
        if content_type is not None:
            params["content_type"] = content_type
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._session.get(
            f"{self.base_url}/media/zhihu/posts",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def media_accounts(self) -> requests.Response:
        """Return configured media accounts."""
        return self._session.get(
            f"{self.base_url}/media/accounts",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def media_create_account(self, name: str, app_id: str | None = None) -> requests.Response:
        """Admin creates a media account (manual, no WeChat API credentials required)."""
        payload: dict = {"name": name}
        if app_id:
            payload["app_id"] = app_id
        return self._session.post(
            f"{self.base_url}/media/accounts",
            json=payload,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    # def media_upload(                          # disabled xlsx upload 2026-06-01
    #     self,
    #     filename: str,
    #     data: bytes,
    #     account_id: int,
    # ) -> requests.Response:
    #     return self._session.post(
    #         f"{self.base_url}/media/upload",
    #         files={"file": (filename, data)},
    #         data={"account_id": str(account_id)},
    #         headers=self._headers(),
    #         timeout=self._timeout(60),
    #     )

    # def media_uploads(self, limit: int = 20) -> requests.Response:  # disabled xlsx upload 2026-06-01
    #     return self._session.get(
    #         f"{self.base_url}/media/uploads",
    #         params={"limit": limit},
    #         headers=self._headers(),
    #         timeout=self._timeout(),
    #     )

    def media_sync_wechat(
        self,
        start_date: str,
        end_date: str,
        account_id: int | None = None,
    ) -> requests.Response:
        """Trigger a WeChat Official Account metrics sync."""
        payload: dict = {"start_date": start_date, "end_date": end_date}
        if account_id is not None:
            payload["account_id"] = account_id
        return self._session.post(
            f"{self.base_url}/media/wechat/sync",
            json=payload,
            headers=self._headers(),
            timeout=self._timeout(240),
        )

    def media_posts(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        account_id: int | None = None,
        q: str | None = None,
    ) -> requests.Response:
        """Return media posts with aggregated metrics."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if account_id:
            params["account_id"] = account_id
        if q:
            params["q"] = q
        return self._session.get(
            f"{self.base_url}/media/posts",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def media_overview(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        account_id: int | None = None,
    ) -> requests.Response:
        """Return media overview metrics."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if account_id:
            params["account_id"] = account_id
        return self._session.get(
            f"{self.base_url}/media/overview",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def media_content_impact(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        window_days: int = 7,
        account_id: int | None = None,
        platform: str | None = None,
    ) -> requests.Response:
        """Return per-article order/revenue lift vs. the pre-publish window."""
        params: dict = {"window_days": window_days}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if account_id:
            params["account_id"] = account_id
        if platform:
            params["platform"] = platform
        return self._session.get(
            f"{self.base_url}/media/content-impact",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def media_source_by_post(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        account_id: int | None = None,
    ) -> requests.Response:
        """Return per-post aggregated source breakdown as {str(post_id): {scene_desc: count}}."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if account_id:
            params["account_id"] = account_id
        return self._session.get(
            f"{self.base_url}/media/source-by-post",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def media_source_breakdown(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        account_id: int | None = None,
    ) -> requests.Response:
        """Return aggregated traffic-source breakdown across posts in the given date range."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if account_id:
            params["account_id"] = account_id
        return self._session.get(
            f"{self.base_url}/media/source-breakdown",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def media_post_metrics(self, post_id: int) -> requests.Response:
        """Return daily metrics for one media post."""
        return self._session.get(
            f"{self.base_url}/media/posts/{post_id}/metrics",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def media_traffic(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        account_id: int | None = None,
        q: str | None = None,
    ) -> requests.Response:
        """Return manually-uploaded article traffic rows."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if account_id:
            params["account_id"] = account_id
        if q:
            params["q"] = q
        return self._session.get(
            f"{self.base_url}/media/traffic",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def media_traffic_overview(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        account_id: int | None = None,
    ) -> requests.Response:
        """Return aggregate overview for manually-uploaded article traffic."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if account_id:
            params["account_id"] = account_id
        return self._session.get(
            f"{self.base_url}/media/traffic/overview",
            params=params,
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def field_coverage(self) -> requests.Response:
        """Return non-null coverage rates for orders columns."""
        return self._session.get(
            f"{self.base_url}/analysis/field_coverage",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def upload_batches(self, limit: int = 10) -> requests.Response:
        """Return recent upload batches (all authenticated users)."""
        return self._session.get(
            f"{self.base_url}/upload/batches",
            params={"limit": limit},
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def upload_summary(self) -> requests.Response:
        """Return per-platform order counts and last upload times (all authenticated users)."""
        return self._session.get(
            f"{self.base_url}/upload/summary",
            headers=self._headers(),
            timeout=self._timeout(),
        )

    def registration_open(self) -> bool:
        """Return True if new sign-ups are allowed."""
        try:
            r = self._session.get(
                f"{self.base_url}/auth/register/open",
                timeout=self._timeout(),
            )
            if r.status_code == 200:
                return bool(r.json().get("allowed"))
        except Exception:
            pass
        return False
