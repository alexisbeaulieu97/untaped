"""Pin load-bearing CONTRIBUTING.md rules by pytest.

- Every credential-named settings leaf is :class:`pydantic.SecretStr` (so
  :func:`redact_secrets` covers it and ``repr(settings)`` won't leak it).
- Every ``HttpClient(...)`` under ``src/`` passes
  ``verify=resolve_verify(...)`` (TLS flows through OS trust +
  ``http.ca_bundle``, never a hard-coded ``True``/``False``/path).
- Tree connectors are drawn only by core's tree module (``untaped.sdk.print_tree``
  and ``tree_text``), never by a private copy.

Each detector is a pure helper fed both the live tree and synthetic
known-bad/known-good inputs, so a detector silently broken to
always-return-empty cannot report "all rules pass".
"""

from __future__ import annotations

import ast
import re

import pytest
from pydantic import BaseModel, SecretStr

from repo.support import REPO_ROOT
from untaped.config_schema import walk_settings
from untaped.settings import get_settings_model

SRC_DIRS = sorted(REPO_ROOT.glob("packages/*/src"))


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


def _resolves_verify(node: ast.expr) -> bool:
    """``resolve_verify(...)``, or ``resolve_verify(...) if v is None else v``.

    The second is a caller's explicit ``verify=`` replacing the settings'.
    """
    if isinstance(node, ast.IfExp):
        test = node.test
        return (
            _is_named_call(node.body, "resolve_verify")
            and isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and isinstance(test.ops[0], ast.Is)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
            and isinstance(node.orelse, ast.Name)
            and node.orelse.id == test.left.id
        )
    return _is_named_call(node, "resolve_verify")


def _verify_offenders(tree: ast.Module, rel: str) -> list[str]:
    """``"<rel>:<lineno>"`` for ``HttpClient(...)`` calls whose ``verify=`` is
    missing or not a ``resolve_verify(...)`` call (or a caller's override of one)."""
    offenders: list[str] = []
    for call in ast.walk(tree):
        if not _is_named_call(call, "HttpClient"):  # type: ignore[arg-type]
            continue
        assert isinstance(call, ast.Call)
        verify_kw = next((kw for kw in call.keywords if kw.arg == "verify"), None)
        if verify_kw is None or not _resolves_verify(verify_kw.value):
            offenders.append(f"{rel}:{call.lineno}")
    return offenders


def test_httpclient_construction_passes_verify() -> None:
    """AST walk, so reformatting and ``# verify=`` comments can't fool it."""
    offenders: list[str] = []
    for py_file in sorted(path for src in SRC_DIRS for path in src.rglob("*.py")):
        text = py_file.read_text(encoding="utf-8")
        if "HttpClient" in text:
            offenders.extend(
                _verify_offenders(ast.parse(text), str(py_file.relative_to(REPO_ROOT)))
            )
    assert not offenders, (
        "HttpClient(...) construction under src/ must pass "
        "verify=resolve_verify(...):\n  " + "\n  ".join(offenders)
    )


_HEADER = "from untaped.sdk import HttpClient, resolve_verify\nimport untaped.sdk as capi\n"


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
        ("HttpClient(base_url='x', verify=resolve_verify(http) if v is None else v)", False),
        ("HttpClient(base_url='x', verify=resolve_verify(http) if v is None else False)", True),
        ("HttpClient(base_url='x', verify=False if v is None else resolve_verify(http))", True),
        ("capi.HttpClient(base_url='x', verify=capi.resolve_verify(http))", False),
    ],
)
def test_verify_detector(call: str, flagged: bool) -> None:
    tree = ast.parse(f"{_HEADER}def f(http) -> None:\n    {call}\n")
    assert bool(_verify_offenders(tree, "<test>")) is flagged


_TREE_MODULE = REPO_ROOT / "packages/untaped/src/untaped/tree.py"
_TREE_CONNECTORS = ("├── ", "└── ")


def _tree_drawers(paths: list[tuple[str, str]]) -> list[str]:
    """``"<rel>"`` for each ``(rel, text)`` of a ``.py`` file that holds a tree connector."""
    return [
        rel
        for rel, text in paths
        if rel.endswith(".py") and any(connector in text for connector in _TREE_CONNECTORS)
    ]


def test_trees_are_drawn_only_by_the_sdk_renderer() -> None:
    files = [
        (str(path.relative_to(REPO_ROOT)), path.read_text(encoding="utf-8"))
        for src in SRC_DIRS
        for path in sorted(src.rglob("*.py"))
        if path != _TREE_MODULE
    ]
    offenders = _tree_drawers(files)
    assert not offenders, (
        "draw trees with untaped.sdk.print_tree or tree_text: one renderer keeps glyphs, the "
        "ASCII fallback, numbering and cycle marks the same everywhere (#789):\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    ("rel", "text", "flagged"),
    [
        ("pkg/render.py", 'PREFIX = "├── "', True),
        ("pkg/render.py", 'LAST = "└── "', True),
        ("pkg/render.py", 'PIPE = "│   "', False),
        # A guide drawing a directory listing is documentation, not a renderer.
        ("pkg/guides/source-ref.md", "├── templates/deploy.yml", False),
    ],
)
def test_tree_drawer_detector(rel: str, text: str, flagged: bool) -> None:
    assert bool(_tree_drawers([(rel, text)])) is flagged
