"""Compatibility import for the shared HTTP API client."""

from app.api_client import APIClient, API_URL, API_VERIFY, DEFAULT_TIMEOUT

__all__ = ["APIClient", "API_URL", "API_VERIFY", "DEFAULT_TIMEOUT"]
