"""Pin load-bearing AGENTS.md rules by pytest.

- Every credential-named settings leaf is :class:`pydantic.SecretStr` (so
  :func:`redact_secrets` covers it and ``repr(settings)`` won't leak it).
- Every ``HttpClient(...)`` under ``src/`` passes
  ``verify=resolve_verify(...)`` (TLS flows through OS trust +
  ``http.ca_bundle``, never a hard-coded ``True``/``False``/path).

Each detector is a pure helper fed both the live tree and synthetic
known-bad/known-good inputs, so a detector silently broken to
always-return-empty cannot report "all rules pass".
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr

from untaped.config_schema import walk_settings
from untaped.settings import get_settings_model

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


# ---- (a) every credential-named field is SecretStr -----------------------

# Word-bounded (``_`` or start/end) so ``tokenize``/``passwordless`` don't
# match; snake_case only, like every settings key in this codebase.
_CREDENTIAL_NAME_RE = re.compile(r"(?:^|_)(token|secret|password|api_key)(?:$|_)")


def _credential_offenders(model_cls: type[BaseModel]) -> list[str]:
    """``"<dotted-key> :: <annotation>"`` for credential-named non-SecretStr
    leaves, walked with the production :func:`walk_settings` (which skips
    collection contents by design)."""
    return [
        f"{descriptor.key} :: {descriptor.annotation!r}"
        for descriptor in walk_settings(model_cls)
        if _CREDENTIAL_NAME_RE.search(descriptor.path[-1].lower()) and not descriptor.is_secret
    ]


def test_credential_fields_are_secretstr() -> None:
    """Complements the SecretStr *inventory* pin in ``test_config_schema.py``:
    this one catches a field named like a credential but typed plain ``str``."""
    offenders = _credential_offenders(get_settings_model())
    assert not offenders, "Credential-named fields must be pydantic.SecretStr:\n  " + "\n  ".join(
        offenders
    )


class _BadSlackInner(BaseModel):
    token: str = "x"


class _BadSlackOuter(BaseModel):
    slack: _BadSlackInner = _BadSlackInner()


class _BadFlatPassword(BaseModel):
    api_password: str = "x"


class _BadApiKey(BaseModel):
    api_key: int = 0  # flagged by name regardless of annotation


class _GoodSchema(BaseModel):
    token: SecretStr = SecretStr("x")
    api_key: SecretStr = SecretStr("y")
    base_url: str = "https://example.com"
    timeout_s: int = 30


@pytest.mark.parametrize(
    ("model_cls", "expected"),
    [
        (_BadSlackOuter, ["slack.token :: <class 'str'>"]),
        (_BadFlatPassword, ["api_password :: <class 'str'>"]),
        (_BadApiKey, ["api_key :: <class 'int'>"]),
        (_GoodSchema, []),
    ],
    ids=["nested-str-token", "flat-str-password", "non-secret-int-api-key", "good"],
)
def test_credential_detector(model_cls: type[BaseModel], expected: list[str]) -> None:
    assert _credential_offenders(model_cls) == expected


# ---- (b) every HttpClient(...) under src/ passes verify= -----------------


def _is_named_call(node: ast.expr, name: str) -> bool:
    """``True`` for ``name(...)`` or ``mod.name(...)`` (structural, not provenance)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == name) or (
        isinstance(func, ast.Attribute) and func.attr == name
    )


def _verify_offenders(tree: ast.Module, rel: str) -> list[str]:
    """``"<rel>:<lineno>"`` for ``HttpClient(...)`` calls whose ``verify=`` is
    missing or not a ``resolve_verify(...)`` call."""
    offenders: list[str] = []
    for call in ast.walk(tree):
        if not _is_named_call(call, "HttpClient"):  # type: ignore[arg-type]
            continue
        assert isinstance(call, ast.Call)
        verify_kw = next((kw for kw in call.keywords if kw.arg == "verify"), None)
        if verify_kw is None or not _is_named_call(verify_kw.value, "resolve_verify"):
            offenders.append(f"{rel}:{call.lineno}")
    return offenders


def test_httpclient_construction_passes_verify() -> None:
    """AST walk, so reformatting and ``# verify=`` comments can't fool it."""
    offenders: list[str] = []
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        if "HttpClient" in text:
            offenders.extend(
                _verify_offenders(ast.parse(text), str(py_file.relative_to(REPO_ROOT)))
            )
    assert not offenders, (
        "HttpClient(...) construction under src/ must pass "
        "verify=resolve_verify(...):\n  " + "\n  ".join(offenders)
    )


_HEADER = (
    "from untaped import HttpClient\nfrom untaped.http import resolve_verify\nimport untaped\n"
)


@pytest.mark.parametrize(
    ("call", "flagged"),
    [
        ("HttpClient(base_url='x', headers={})", True),
        ("HttpClient(base_url='x', verify=True)", True),
        ("HttpClient(base_url='x', verify=False)", True),
        ("HttpClient(base_url='x', verify='/etc/ssl/cert.pem')", True),
        ("HttpClient(base_url='x', verify=custom())", True),
        # Regression: the bare, uncalled reference must not be accepted.
        ("HttpClient(base_url='x', verify=resolve_verify)", True),
        ("HttpClient(base_url='x', verify=resolve_verify(http))", False),
        ("untaped.HttpClient(base_url='x', verify=untaped.resolve_verify(http))", False),
    ],
)
def test_verify_detector(call: str, flagged: bool) -> None:
    tree = ast.parse(f"{_HEADER}def f(http) -> None:\n    {call}\n")
    assert bool(_verify_offenders(tree, "<test>")) is flagged
