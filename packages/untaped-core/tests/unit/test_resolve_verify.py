"""Tests for :func:`resolve_verify` — TLS resolution policy."""

import ssl
from pathlib import Path

from untaped_core.http import resolve_verify
from untaped_core.settings import HttpSettings


def test_returns_false_when_verify_disabled() -> None:
    assert resolve_verify(HttpSettings(verify_ssl=False)) is False


def test_returns_path_when_ca_bundle_set() -> None:
    bundle = Path("/etc/ssl/corp-ca.pem")
    result = resolve_verify(HttpSettings(ca_bundle=bundle))
    assert result == str(bundle)


def test_ca_bundle_takes_precedence_over_default(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("dummy")
    result = resolve_verify(HttpSettings(ca_bundle=bundle))
    assert result == str(bundle)


def test_default_returns_truststore_ssl_context() -> None:
    ctx = resolve_verify(HttpSettings())
    assert isinstance(ctx, ssl.SSLContext)


def test_disabled_beats_ca_bundle() -> None:
    """If the user explicitly disables verification, ca_bundle is irrelevant."""
    result = resolve_verify(HttpSettings(verify_ssl=False, ca_bundle=Path("/x.pem")))
    assert result is False


def test_ca_bundle_expanduser() -> None:
    result = resolve_verify(HttpSettings(ca_bundle=Path("~/ca.pem")))
    assert isinstance(result, str)
    assert "~" not in result
