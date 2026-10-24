"""Tests for :func:`resolve_verify` — TLS resolution policy."""

import ssl
from pathlib import Path

import certifi
import pytest
from pydantic import BaseModel

from untaped.runtime.settings import HttpSettings
from untaped.sdk.errors import ConfigError
from untaped.sdk.network.http import connected_client, resolve_verify


def _write_valid_bundle(path: Path) -> None:
    """Write a loadable PEM bundle (certifi's, shipped with httpx)."""
    path.write_text(Path(certifi.where()).read_text())


def test_ca_bundle_takes_precedence_over_default(tmp_path: Path) -> None:
    bundle = tmp_path / "corp-ca.pem"
    _write_valid_bundle(bundle)
    assert resolve_verify(HttpSettings(ca_bundle_path=bundle)) == str(bundle)


def test_default_returns_truststore_ssl_context() -> None:
    ctx = resolve_verify(HttpSettings())
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True


def test_ca_bundle_expanduser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_valid_bundle(tmp_path / "ca.pem")
    assert resolve_verify(HttpSettings(ca_bundle_path=Path("~/ca.pem"))) == str(tmp_path / "ca.pem")


# ── unreadable / invalid bundles are config errors, not tracebacks ───────────


@pytest.mark.parametrize("content", [None, "not a certificate"], ids=["missing", "invalid"])
def test_bad_ca_bundle_is_a_config_error_naming_the_path(
    tmp_path: Path, content: str | None
) -> None:
    bundle = tmp_path / "bad.pem"
    if content is not None:
        bundle.write_text(content)
    with pytest.raises(ConfigError, match=r"http\.ca_bundle_path.*bad\.pem"):
        resolve_verify(HttpSettings(ca_bundle_path=bundle))


def test_unreadable_ca_bundle_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "locked.pem"
    bundle.write_text("x")

    def deny(*_args: object, **_kwargs: object) -> ssl.SSLContext:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ssl, "create_default_context", deny)
    with pytest.raises(ConfigError, match=r"http\.ca_bundle_path.*locked\.pem"):
        resolve_verify(HttpSettings(ca_bundle_path=bundle))


def test_http_client_with_invalid_ca_bundle_raises_config_error(tmp_path: Path) -> None:
    class Conn(BaseModel):
        base_url: str = "https://example.invalid"
        token: str = "t"

    bundle = tmp_path / "garbage.pem"
    bundle.write_text("not a certificate")
    with pytest.raises(ConfigError, match=r"http\.ca_bundle_path"):
        connected_client(
            Conn(),
            section="demo",
            base_url=Conn().base_url,
            http=HttpSettings(ca_bundle_path=bundle),
        )
