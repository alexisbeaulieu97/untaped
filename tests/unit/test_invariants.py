"""Pin load-bearing AGENTS.md Hard Rules by pytest.

Two invariants documented in ``AGENTS.md`` are honoured by convention
today and would silently bit-rot if core drifted. Plugin repos carry their
own copies of plugin-specific invariants.

- :func:`test_credential_fields_are_secretstr` — Hard Rule #11.
  Every leaf on the registered settings model whose name implies a
  credential is typed :class:`pydantic.SecretStr` (so
  :func:`redact_secrets` covers it and ``repr(settings)`` won't leak
  it in tracebacks).
- :func:`test_httpclient_construction_passes_verify` — Hard Rule #12.
  Every ``HttpClient(...)`` call under ``src/`` passes
  ``verify=resolve_verify(...)`` (so TLS defaults flow through OS trust +
  ``http.ca_bundle``, never a hard-coded ``True`` / ``False`` / path).
Each detector is factored into a pure helper (``_credential_offenders``,
``_verify_offenders``); the positive tests at the bottom feed the live workspace state through
those helpers, and parametrised negative self-tests feed synthetic
known-bad inputs so each detector is provably wired (sibling pattern to
``test_layering.py::_BYPASS_SOURCES``). Without the negative tests, a
detector silently broken to always-return-empty would still report
"all rules pass" — defeating the pin.

Follows the same ``REPO_ROOT = parents[2]`` discovery pattern as the other
core guard tests.
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

# Matches credential-implying leaf names: ``token``, ``api_token``,
# ``access_token``, ``client_secret``, ``password``, ``api_key``, ...
# Anchored to word boundaries (``_`` or start/end) so an unrelated name
# like ``tokenize`` or ``passwordless`` wouldn't false-positive. Scope
# is deliberately snake_case-only — pydantic settings keys follow that
# convention; ``clientSecret``-style camelCase would slip through but
# is not idiomatic in this codebase.
_CREDENTIAL_NAME_RE = re.compile(r"(?:^|_)(token|secret|password|api_key)(?:$|_)")


def _credential_offenders(model_cls: type[BaseModel]) -> list[str]:
    """Return ``"<dotted-key> :: <annotation>"`` rows for credential-named
    leaves on ``model_cls`` that are *not* typed ``SecretStr``.

    Walks via :func:`walk_settings` — the same path the live ``Settings``
    walk uses — so the synthetic negative-test schemas exercise the
    production code, with no parallel walker that could drift.

    **Scope.** ``walk_settings`` only recurses through nested
    ``BaseModel`` fields; leaves inside ``list[...]``, ``dict[...]``, or
    other collections are skipped (per :func:`walk_settings`'s
    documented behaviour). A credential-named leaf hidden inside a
    collection would not be flagged here — by design, since collection
    contents are managed by domain-specific commands rather than the
    settings schema.
    """
    offenders: list[str] = []
    for descriptor in walk_settings(model_cls):
        leaf_name = descriptor.path[-1].lower()
        if not _CREDENTIAL_NAME_RE.search(leaf_name):
            continue
        if not descriptor.is_secret:
            offenders.append(f"{descriptor.key} :: {descriptor.annotation!r}")
    return offenders


def test_credential_fields_are_secretstr() -> None:
    """Every leaf whose name implies a credential must be ``SecretStr``.

    Hard Rule #11. Walks the live registered settings schema via the existing
    :func:`walk_settings` helper. A new domain adding ``slack.token: str``
    fails here with the offending dotted key.

    **Complement to**
    ``test_secret_field_paths_matches_known_settings_secrets`` in
    ``tests/unit/test_config_schema.py``: that
    test pins the *inventory* of declared ``SecretStr`` paths (count +
    membership). This one catches the opposite mistake — a field
    *named* like a credential but *typed* as plain ``str``, which the
    inventory pin can't see because the field never makes it into
    ``secret_field_paths(...)``. Keep both.
    """
    offenders = _credential_offenders(get_settings_model())
    assert not offenders, (
        "Credential-named fields must be pydantic.SecretStr "
        "(see AGENTS.md Hard Rule #11):\n  " + "\n  ".join(offenders)
    )


# ---- (b) every HttpClient(...) under src/ passes verify= -----------------


def _httpclient_calls_in(tree: ast.Module) -> list[ast.Call]:
    """Return every ``HttpClient(...)`` constructor call in ``tree``.

    Matches both bare ``HttpClient(...)`` (after ``from untaped
    import HttpClient``) and attribute-style ``untaped.HttpClient(...)``.
    """
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id == "HttpClient") or (
            isinstance(func, ast.Attribute) and func.attr == "HttpClient"
        ):
            calls.append(node)
    return calls


def _is_resolve_verify_call(node: ast.expr) -> bool:
    """``True`` if ``node`` is a call to a name ending in ``resolve_verify``.

    Accepts both ``resolve_verify(...)`` (after ``from untaped.http
    import resolve_verify``) and ``mod.resolve_verify(...)``. Other
    shapes — bare ``True``/``False``, a string path, a different
    callable, or the bare uncalled reference — are rejected so AGENTS.md
    Hard Rule #12's prohibition on hard-coded verify values is
    structurally enforced, not just the weaker "kwarg present" version
    this test originally checked.

    Match is **structural, not provenance-based** — it doesn't trace the
    name back to ``untaped.http.resolve_verify`` through imports, so
    a contrived homonym (``other_lib.resolve_verify``) would pass. And
    arity isn't checked: a zero-arg ``resolve_verify()`` would pass here
    and fail-fast at runtime instead. Both are acceptable for a pin —
    they keep the AST predicate small without sacrificing the
    "hard-coded verify" prohibition the rule actually enforces.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "resolve_verify"
    if isinstance(func, ast.Attribute):
        return func.attr == "resolve_verify"
    return False


def _verify_offenders(tree: ast.Module, rel: str) -> list[str]:
    """Return ``"<rel>:<lineno>"`` rows for ``HttpClient(...)`` calls in
    ``tree`` whose ``verify=`` argument is missing or not a
    ``resolve_verify(...)`` call.
    """
    offenders: list[str] = []
    for call in _httpclient_calls_in(tree):
        verify_kw = next((kw for kw in call.keywords if kw.arg == "verify"), None)
        if verify_kw is None or not _is_resolve_verify_call(verify_kw.value):
            offenders.append(f"{rel}:{call.lineno}")
    return offenders


def test_httpclient_construction_passes_verify() -> None:
    """Every ``HttpClient(...)`` under ``src/`` must pass
    ``verify=resolve_verify(...)``.

    Hard Rule #12. AST walk over core source
    so the check survives reformatting and ignores ``# verify=`` style
    comments that a string-match regex would be fooled by. A new client
    that hard-codes ``verify=False`` or forgets the kwarg entirely fails
    here with the ``file:line`` of the offending call.
    """
    offenders: list[str] = []
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        # Cheap skip: most files don't construct HttpClient. False hits
        # (e.g. "HttpClient" in a comment) still parse and then find no call.
        if "HttpClient" not in text:
            continue
        tree = ast.parse(text)
        rel = str(py_file.relative_to(REPO_ROOT))
        offenders.extend(_verify_offenders(tree, rel))
    assert not offenders, (
        "HttpClient(...) construction under src/ must pass "
        "verify=resolve_verify(...) (see AGENTS.md Hard Rule #12):\n  " + "\n  ".join(offenders)
    )


# ---- negative self-tests: each detector fires on a known-bad input -------
#
# Without these, a detector silently broken to always-return-empty would
# still report "all rules pass" and the positive tests above would not
# catch it. Sibling shape to ``test_layering.py``'s ``_BYPASS_SOURCES``.


# (a) Credential-named leaves typed as plain ``str`` (or any non-``SecretStr``).
# Each entry's nested model carries one credential-named leaf that
# violates Hard Rule #11; the detector must flag it.


class _BadSlackInner(BaseModel):
    token: str = "x"


class _BadSlackOuter(BaseModel):
    slack: _BadSlackInner = _BadSlackInner()


class _BadFlatPassword(BaseModel):
    api_password: str = "x"


class _BadApiKey(BaseModel):
    api_key: int = 0  # not str, not SecretStr — still a credential by name


# Each row carries the *exact* offender string the detector should emit, so
# the assertion catches a future bug that returns "something" but with the
# wrong shape (off-by-one column, wrong field rendered, etc.). The `int`
# case is what proves the detector flags by name regardless of annotation.
_CREDENTIAL_BAD_SCHEMAS: list[tuple[str, type[BaseModel], str]] = [
    ("nested-str-token", _BadSlackOuter, "slack.token :: <class 'str'>"),
    ("flat-str-password", _BadFlatPassword, "api_password :: <class 'str'>"),
    ("non-secret-int-api-key", _BadApiKey, "api_key :: <class 'int'>"),
]


@pytest.mark.parametrize(
    ("label", "model_cls", "expected"),
    _CREDENTIAL_BAD_SCHEMAS,
    ids=[lbl for lbl, _, _ in _CREDENTIAL_BAD_SCHEMAS],
)
def test_credential_detector_flags_bad_schemas(
    label: str, model_cls: type[BaseModel], expected: str
) -> None:
    """The detector must flag every credential-named non-``SecretStr`` shape.

    Each fixture is constructed with exactly one offending leaf, so equality
    (not containment) is the tight assertion — a future bug that emits
    spurious extra offenders would fail here, not silently pass.
    """
    assert _credential_offenders(model_cls) == [expected], f"{label}: wrong offender list"


class _GoodSecretSchema(BaseModel):
    token: SecretStr = SecretStr("x")
    api_key: SecretStr = SecretStr("y")


class _GoodNonCredentialSchema(BaseModel):
    # Leaf names that do NOT match the credential regex — must not be
    # flagged even though they're plain ``str``.
    base_url: str = "https://example.com"
    timeout_s: int = 30


def test_credential_detector_ignores_secretstr_and_non_credential_names() -> None:
    """Legitimate shapes must not be falsely flagged."""
    assert _credential_offenders(_GoodSecretSchema) == []
    assert _credential_offenders(_GoodNonCredentialSchema) == []


# (b) HttpClient construction sources. Each entry is (label, source) —
# the source simulates a future contributor's infra file shape; the
# detector must flag the offending HttpClient(...) call.

_VERIFY_BAD_SOURCES: list[tuple[str, str]] = [
    (
        "missing-verify-kwarg",
        "from untaped import HttpClient\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', headers={})\n",
    ),
    (
        "hardcoded-verify-true",
        "from untaped import HttpClient\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', verify=True)\n",
    ),
    (
        "hardcoded-verify-false",
        "from untaped import HttpClient\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', verify=False)\n",
    ),
    (
        "hardcoded-ca-bundle-path",
        "from untaped import HttpClient\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', verify='/etc/ssl/cert.pem')\n",
    ),
    (
        "other-callable-not-resolve-verify",
        "from untaped import HttpClient\n"
        "def custom() -> bool: return True\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', verify=custom())\n",
    ),
    (
        # Regression: bare reference (not called). The predicate gates on
        # ``isinstance(node, ast.Call)`` first, so the bare ``resolve_verify``
        # name is correctly rejected — pin that here so a future "just
        # accept the name" simplification fails loudly.
        "bare-resolve-verify-reference",
        "from untaped import HttpClient\n"
        "from untaped.http import resolve_verify\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', verify=resolve_verify)\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"),
    _VERIFY_BAD_SOURCES,
    ids=[lbl for lbl, _ in _VERIFY_BAD_SOURCES],
)
def test_verify_detector_flags_bad_sources(label: str, source: str) -> None:
    """The detector must flag every non-``resolve_verify(...)`` call shape."""
    tree = ast.parse(source)
    assert _verify_offenders(tree, "<test>"), f"expected {label} to be flagged"


_VERIFY_GOOD_SOURCES: list[tuple[str, str]] = [
    (
        "bare-resolve-verify-call",
        "from untaped import HttpClient\n"
        "from untaped.http import resolve_verify\n"
        "def f(http) -> None:\n"
        "    HttpClient(base_url='x', verify=resolve_verify(http))\n",
    ),
    (
        "attribute-resolve-verify-call",
        "import untaped\n"
        "def f(http) -> None:\n"
        "    untaped.HttpClient(base_url='x', verify=untaped.resolve_verify(http))\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"),
    _VERIFY_GOOD_SOURCES,
    ids=[lbl for lbl, _ in _VERIFY_GOOD_SOURCES],
)
def test_verify_detector_ignores_resolve_verify(label: str, source: str) -> None:
    """The canonical ``verify=resolve_verify(...)`` call shape must pass."""
    tree = ast.parse(source)
    assert _verify_offenders(tree, "<test>") == [], f"expected {label} to be accepted"
