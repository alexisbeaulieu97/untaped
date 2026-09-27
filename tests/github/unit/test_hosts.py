"""Tests for deriving the GitHub Git host from the configured API base URL."""

from __future__ import annotations

from untaped.capabilities.github.domain import github_web_host


def test_github_web_host_derives_from_api_base_url() -> None:
    assert github_web_host("https://api.github.com") == "github.com"
    assert github_web_host("https://ghe.example.com/api/v3") == "ghe.example.com"
    assert github_web_host("https://api.acme.ghe.com/") == "acme.ghe.com"
    assert github_web_host("not a url") is None


def test_github_web_host_keeps_api_prefixed_enterprise_server_host() -> None:
    # A GHES host that happens to start with ``api.`` serves the web UI on
    # that same host; only API-subdomain bases drop the ``api.`` label.
    assert github_web_host("https://api.corp.example.com/api/v3") == "api.corp.example.com"
    assert github_web_host("https://api.corp.example.com/api/v3/") == "api.corp.example.com"
