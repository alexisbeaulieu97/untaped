"""Tests for :func:`resolve_verify` — TLS resolution policy."""

import shutil
import ssl
import subprocess
from pathlib import Path

import certifi
import pytest
from pydantic import BaseModel

from untaped.errors import ConfigError
from untaped.http import connected_client, resolve_verify
from untaped.settings import HttpSettings


def _write_valid_bundle(path: Path) -> None:
    """Write a loadable PEM bundle (certifi's, shipped with httpx)."""
    path.write_text(Path(certifi.where()).read_text())


def test_returns_false_when_verify_disabled() -> None:
    assert resolve_verify(HttpSettings(verify_ssl=False)) is False


def test_returns_path_when_ca_bundle_set(tmp_path: Path) -> None:
    bundle = tmp_path / "corp-ca.pem"
    _write_valid_bundle(bundle)
    result = resolve_verify(HttpSettings(ca_bundle=bundle))
    assert result == str(bundle)


@pytest.mark.parametrize("verify_hostname", [True, False])
def test_missing_ca_bundle_is_a_config_error_naming_the_path(
    tmp_path: Path, verify_hostname: bool
) -> None:
    bundle = tmp_path / "missing.pem"
    with pytest.raises(ConfigError, match=r"http\.ca_bundle.*missing\.pem"):
        resolve_verify(HttpSettings(ca_bundle=bundle, verify_hostname=verify_hostname))


def test_ca_bundle_takes_precedence_over_default(tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    _write_valid_bundle(bundle)
    result = resolve_verify(HttpSettings(ca_bundle=bundle))
    assert result == str(bundle)


def test_default_returns_truststore_ssl_context() -> None:
    ctx = resolve_verify(HttpSettings())
    assert isinstance(ctx, ssl.SSLContext)


def test_disabled_beats_ca_bundle() -> None:
    """If the user explicitly disables verification, ca_bundle is irrelevant."""
    result = resolve_verify(HttpSettings(verify_ssl=False, ca_bundle=Path("/x.pem")))
    assert result is False


def test_ca_bundle_expanduser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_valid_bundle(tmp_path / "ca.pem")
    result = resolve_verify(HttpSettings(ca_bundle=Path("~/ca.pem")))
    assert isinstance(result, str)
    assert "~" not in result


# ── verify_hostname: self-signed certs that fail modern Python's SAN check ─────


def _self_signed_cert(tmp_path: Path) -> Path:
    """Mint a throwaway self-signed cert (CN-only, no SAN — the failing case)."""
    openssl = shutil.which("openssl")
    assert openssl is not None, "callers guard on shutil.which('openssl')"
    cert = tmp_path / "self-signed.pem"
    key = tmp_path / "self-signed.key"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=internal.invalid",
        ],
        check=True,
        capture_output=True,
    )
    return cert


def test_verify_hostname_defaults_to_true() -> None:
    assert HttpSettings().verify_hostname is True


def test_verify_hostname_false_skips_hostname_check_keeping_encryption() -> None:
    """A self-signed cert that fails the SAN/hostname check on modern Python:
    drop the hostname binding but keep chain verification — strictly safer
    than ``verify_ssl=False`` (which trusts anything)."""
    ctx = resolve_verify(HttpSettings(verify_hostname=False))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_verify_ssl_false_beats_verify_hostname() -> None:
    """Disabling verification entirely wins over the narrower hostname toggle."""
    assert resolve_verify(HttpSettings(verify_ssl=False, verify_hostname=False)) is False


def test_verify_hostname_true_is_unchanged_default_path() -> None:
    """The default (hostname on, no ca_bundle) still returns a truststore context."""
    ctx = resolve_verify(HttpSettings(verify_hostname=True))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl to mint a cert")
def test_verify_hostname_false_with_ca_bundle_loads_cert_and_skips_hostname(
    tmp_path: Path,
) -> None:
    cert = _self_signed_cert(tmp_path)
    ctx = resolve_verify(HttpSettings(ca_bundle=cert, verify_hostname=False))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    # The self-signed cert is actually loaded into the trust store.
    assert ctx.get_ca_certs(), "ca_bundle should be loaded into the context"


# ── unreadable / invalid bundles are config errors, not tracebacks ───────────


@pytest.mark.parametrize("verify_hostname", [True, False])
def test_invalid_ca_bundle_is_a_config_error(tmp_path: Path, verify_hostname: bool) -> None:
    bundle = tmp_path / "garbage.pem"
    bundle.write_text("not a certificate")
    with pytest.raises(ConfigError, match=r"http\.ca_bundle.*garbage\.pem"):
        resolve_verify(HttpSettings(ca_bundle=bundle, verify_hostname=verify_hostname))


def test_unreadable_ca_bundle_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "locked.pem"
    bundle.write_text("x")

    def deny(*_args: object, **_kwargs: object) -> ssl.SSLContext:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ssl, "create_default_context", deny)
    with pytest.raises(ConfigError, match=r"http\.ca_bundle.*locked\.pem"):
        resolve_verify(HttpSettings(ca_bundle=bundle, verify_hostname=False))


def test_http_client_with_invalid_ca_bundle_raises_config_error(tmp_path: Path) -> None:
    class Conn(BaseModel):
        base_url: str = "https://example.invalid"
        token: str = "t"

    bundle = tmp_path / "garbage.pem"
    bundle.write_text("not a certificate")
    with pytest.raises(ConfigError, match=r"http\.ca_bundle"):
        connected_client(
            Conn(), section="demo", http=HttpSettings(ca_bundle=bundle, verify_hostname=False)
        )
