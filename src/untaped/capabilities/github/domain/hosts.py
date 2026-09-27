"""GitHub host derivation from the configured REST API base URL."""

from __future__ import annotations

from urllib.parse import urlparse


def github_web_host(base_url: str) -> str | None:
    """Derive the Git web host from a GitHub REST API base URL.

    ``https://api.github.com`` -> ``github.com``; GitHub Enterprise Server
    ``https://ghe.example.com/api/v3`` -> ``ghe.example.com``; data-residency
    ``https://api.acme.ghe.com`` -> ``acme.ghe.com``. A ``/api/v3`` base is
    served from the web host itself, so its host is kept even when it starts
    with ``api.`` (``https://api.corp.example.com/api/v3``).
    """
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if parsed.path.rstrip("/").lower().endswith("/api/v3"):
        return host
    return host.removeprefix("api.")
