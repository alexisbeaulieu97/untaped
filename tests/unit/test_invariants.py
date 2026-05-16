"""Pin load-bearing AGENTS.md Hard Rules by pytest.

Three invariants documented in ``AGENTS.md`` are honoured by convention
today and would silently bit-rot if a new domain forgot them. Each
function below asserts one rule by walking the workspace at test time,
so a freshly added domain is automatically covered without a test
edit:

- :func:`test_credential_fields_are_secretstr` — Hard Rule #11.
  Every leaf on ``untaped_core.Settings`` whose name implies a
  credential is typed :class:`pydantic.SecretStr` (so
  :func:`redact_secrets` covers it and ``repr(settings)`` won't leak
  it in tracebacks).
- :func:`test_httpclient_construction_passes_verify` — Hard Rule #12.
  Every ``HttpClient(...)`` call under
  ``packages/*/src/*/infrastructure/`` passes ``verify=resolve_verify(...)``
  (so TLS defaults flow through OS trust + ``http.ca_bundle``, never a
  hard-coded ``True`` / ``False`` / path).
- :func:`test_typer_apps_and_required_arg_commands_set_no_args_is_help` —
  Hard Rule #9. Every ``typer.Typer`` app and every command with at
  least one required argument sets ``no_args_is_help=True`` (so
  no-args invocation shows help instead of erroring).

Each detector is factored into a pure helper (``_credential_offenders``,
``_verify_offenders``, ``_no_args_help_offenders``); the
positive tests at the bottom feed the live workspace state through
those helpers, and parametrised negative self-tests feed synthetic
known-bad inputs so each detector is provably wired (sibling pattern to
``test_layering.py::_BYPASS_SOURCES``). Without the negative tests, a
detector silently broken to always-return-empty would still report
"all rules pass" — defeating the pin.

Sibling of ``test_layering.py`` / ``test_import_linter_contracts.py``;
follows the same ``REPO_ROOT = parents[2]`` discovery pattern so the
checks are workspace-wide by construction.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any

import pytest
import typer
from pydantic import BaseModel, SecretStr
from untaped_core import Settings, walk_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "packages"


# ---- (a) every credential-named field on Settings is SecretStr -----------

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

    Hard Rule #11. Walks the live ``Settings`` schema via the existing
    :func:`walk_settings` helper. A new domain adding ``slack.token: str``
    fails here with the offending dotted key.

    **Complement to**
    ``test_secret_field_paths_matches_known_settings_secrets`` in
    ``packages/untaped-core/tests/unit/test_config_schema.py``: that
    test pins the *inventory* of declared ``SecretStr`` paths (count +
    membership). This one catches the opposite mistake — a field
    *named* like a credential but *typed* as plain ``str``, which the
    inventory pin can't see because the field never makes it into
    ``secret_field_paths(...)``. Keep both.
    """
    offenders = _credential_offenders(Settings)
    assert not offenders, (
        "Credential-named fields must be pydantic.SecretStr "
        "(see AGENTS.md Hard Rule #11):\n  " + "\n  ".join(offenders)
    )


# ---- (b) every HttpClient(...) under infrastructure/ passes verify= ------


def _httpclient_calls_in(tree: ast.Module) -> list[ast.Call]:
    """Return every ``HttpClient(...)`` constructor call in ``tree``.

    Matches both bare ``HttpClient(...)`` (after ``from untaped_core
    import HttpClient``) and attribute-style ``untaped_core.HttpClient(...)``.
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

    Accepts both ``resolve_verify(...)`` (after ``from untaped_core.http
    import resolve_verify``) and ``mod.resolve_verify(...)``. Other
    shapes — bare ``True``/``False``, a string path, a different
    callable, or the bare uncalled reference — are rejected so AGENTS.md
    Hard Rule #12's prohibition on hard-coded verify values is
    structurally enforced, not just the weaker "kwarg present" version
    this test originally checked.

    Match is **structural, not provenance-based** — it doesn't trace the
    name back to ``untaped_core.http.resolve_verify`` through imports, so
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
    """Every ``HttpClient(...)`` under ``infrastructure/`` must pass
    ``verify=resolve_verify(...)``.

    Hard Rule #12. AST walk over every domain's ``infrastructure/`` tree
    so the check survives reformatting and ignores ``# verify=`` style
    comments that a string-match regex would be fooled by. A new client
    that hard-codes ``verify=False`` or forgets the kwarg entirely fails
    here with the ``file:line`` of the offending call.

    **Scope.** Domain ``infrastructure/`` layers only. ``src/untaped/``
    (the root binary) is excluded by design — it is ``add_typer``-only
    aggregation and does not construct HTTP clients. If that ever
    changes, widen the glob.
    """
    offenders: list[str] = []
    for infra_dir in sorted(PACKAGES_DIR.glob("*/src/*/infrastructure")):
        for py_file in sorted(infra_dir.rglob("*.py")):
            text = py_file.read_text(encoding="utf-8")
            # Cheap skip: most infra files don't construct HttpClient.
            # Substring check elides ~90% of ast.parse calls without
            # narrowing the workspace-wide discovery glob — false hits
            # (e.g. "HttpClient" in a comment) still parse and then
            # find no call.
            if "HttpClient" not in text:
                continue
            tree = ast.parse(text)
            rel = str(py_file.relative_to(REPO_ROOT))
            offenders.extend(_verify_offenders(tree, rel))
    assert not offenders, (
        "HttpClient(...) construction under infrastructure/ must pass "
        "verify=resolve_verify(...) (see AGENTS.md Hard Rule #12):\n  " + "\n  ".join(offenders)
    )


# ---- (c) typer apps and required-arg commands set no_args_is_help --------


def _walk_typer(app: typer.Typer, prefix: str = "") -> list[tuple[str, Any]]:
    """Return every ``(qualified_name, item)`` reachable from ``app``.

    ``item`` is either a ``typer.Typer`` instance (sub-app) or a Typer
    ``CommandInfo`` (leaf command); the caller dispatches on type. Names
    are space-separated so a failure message reads like the actual
    command path (``awx job-templates launch``).

    Accesses ``app.registered_groups`` / ``registered_commands`` directly
    (no defensive ``getattr``) — both attributes are set unconditionally
    in ``typer.Typer.__init__``. A future Typer rename should fail loudly
    here, not silently report "all good", which matches the issue body's
    "fails loudly on Typer upgrade" intent.
    """
    out: list[tuple[str, Any]] = []
    for group in app.registered_groups:
        sub = group.typer_instance
        out.append((f"{prefix}{group.name}", sub))
        out.extend(_walk_typer(sub, prefix=f"{prefix}{group.name} "))
    for cmd in app.registered_commands:
        name = cmd.name or cmd.callback.__name__
        out.append((f"{prefix}{name}", cmd))
    return out


def _has_required_arg(callback: Any) -> bool:
    """Return True if ``callback`` has at least one user-required parameter.

    Typer wraps every parameter's default in an ``ArgumentInfo`` /
    ``OptionInfo`` whose ``.default`` carries the *real* default. A
    bare ``...`` (Ellipsis) means "no default — user must supply",
    which maps directly to "user-required argument".

    Deliberately *doesn't* fall back on ``Parameter.empty`` for
    unwrapped params: this helper only runs against commands
    already registered on a ``typer.Typer`` app, where Typer
    guarantees every param carries a wrapper. The ``Parameter.empty``
    case would also false-positive on Typer-injected parameters like
    ``ctx: typer.Context``, which the user never supplies on the
    command line.
    """
    sig = inspect.signature(callback)
    for p in sig.parameters.values():
        info = p.default
        if (
            isinstance(info, (typer.models.ArgumentInfo, typer.models.OptionInfo))
            and info.default is ...
        ):
            return True
    return False


def _no_args_help_offenders(root_app: typer.Typer) -> list[str]:
    """Return ``"app: <path>"`` / ``"command: <path>"`` rows for every
    Typer app or required-arg command reachable from ``root_app`` that
    does not set ``no_args_is_help=True``.
    """
    offenders: list[str] = []
    # Seed the walk with the root app so the loop handles every entry
    # uniformly — no special-case for the root before the loop.
    for name, item in [("<root>", root_app), *_walk_typer(root_app)]:
        if isinstance(item, typer.Typer):
            if not item.info.no_args_is_help:
                offenders.append(f"app: {name}")
            continue
        if _has_required_arg(item.callback) and not item.no_args_is_help:
            offenders.append(f"command: {name}")
    return offenders


def test_typer_apps_and_required_arg_commands_set_no_args_is_help() -> None:
    """Every Typer app and every required-arg command sets ``no_args_is_help=True``.

    Hard Rule #9. Imports the live root app (which pulls every domain
    via :mod:`untaped.main`), walks its groups + commands, and reports
    any offender with its qualified command path.
    """
    from untaped.main import app as root_app

    offenders = _no_args_help_offenders(root_app)
    assert not offenders, (
        "Typer apps and required-arg commands must set no_args_is_help=True "
        "(see AGENTS.md Hard Rule #9):\n  " + "\n  ".join(offenders)
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
        "from untaped_core import HttpClient\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', headers={})\n",
    ),
    (
        "hardcoded-verify-true",
        "from untaped_core import HttpClient\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', verify=True)\n",
    ),
    (
        "hardcoded-verify-false",
        "from untaped_core import HttpClient\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', verify=False)\n",
    ),
    (
        "hardcoded-ca-bundle-path",
        "from untaped_core import HttpClient\n"
        "def f() -> None:\n"
        "    HttpClient(base_url='x', verify='/etc/ssl/cert.pem')\n",
    ),
    (
        "other-callable-not-resolve-verify",
        "from untaped_core import HttpClient\n"
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
        "from untaped_core import HttpClient\n"
        "from untaped_core.http import resolve_verify\n"
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
        "from untaped_core import HttpClient\n"
        "from untaped_core.http import resolve_verify\n"
        "def f(http) -> None:\n"
        "    HttpClient(base_url='x', verify=resolve_verify(http))\n",
    ),
    (
        "attribute-resolve-verify-call",
        "import untaped_core\n"
        "def f(http) -> None:\n"
        "    untaped_core.HttpClient(base_url='x', verify=untaped_core.resolve_verify(http))\n",
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


# (c) Typer apps / commands missing ``no_args_is_help=True``. Synthetic
# Typer apps; the detector must flag the offending app and/or command.


def _required_arg_callback(name: str = typer.Argument(...)) -> None:
    """Synthetic Typer command body — one required positional."""


def _optional_arg_callback(name: str = typer.Option("default")) -> None:
    """Synthetic Typer command body — no required arg."""


def test_no_args_help_detector_flags_sub_app_missing_flag() -> None:
    """A sub-app without ``no_args_is_help=True`` must be flagged."""
    root = typer.Typer(no_args_is_help=True)
    sub = typer.Typer()  # missing no_args_is_help
    root.add_typer(sub, name="sub")
    offenders = _no_args_help_offenders(root)
    assert "app: sub" in offenders


def test_no_args_help_detector_walks_nested_sub_apps() -> None:
    """``_walk_typer`` must recurse — an offender at depth 2 is still flagged.

    Pins the prefix-rendering ("sub subsub", space-separated) so a future
    "optimise away the recursion" refactor fails loudly.
    """
    root = typer.Typer(no_args_is_help=True)
    sub = typer.Typer(no_args_is_help=True)
    subsub = typer.Typer()  # missing the flag at depth 2
    sub.add_typer(subsub, name="subsub")
    root.add_typer(sub, name="sub")
    offenders = _no_args_help_offenders(root)
    assert "app: sub subsub" in offenders


def test_no_args_help_detector_flags_required_arg_command_missing_flag() -> None:
    """A required-arg command without ``no_args_is_help=True`` must be flagged."""
    root = typer.Typer(no_args_is_help=True)
    root.command("hit")(_required_arg_callback)  # missing no_args_is_help
    offenders = _no_args_help_offenders(root)
    assert "command: hit" in offenders


def test_no_args_help_detector_flags_root_missing_flag() -> None:
    """The root app itself, if missing ``no_args_is_help=True``, must be flagged."""
    root = typer.Typer()  # missing no_args_is_help
    offenders = _no_args_help_offenders(root)
    assert "app: <root>" in offenders


def test_no_args_help_detector_ignores_optional_arg_command_without_flag() -> None:
    """A command with only optional args is exempt — bare invocation runs it."""
    root = typer.Typer(no_args_is_help=True)
    root.command("ok")(_optional_arg_callback)  # no required arg → flag not needed
    offenders = _no_args_help_offenders(root)
    assert offenders == []


def test_no_args_help_detector_accepts_canonical_shape() -> None:
    """The canonical "every app + every required-arg command has the flag" shape passes."""
    root = typer.Typer(no_args_is_help=True)
    sub = typer.Typer(no_args_is_help=True)
    root.add_typer(sub, name="sub")
    sub.command("hit", no_args_is_help=True)(_required_arg_callback)
    offenders = _no_args_help_offenders(root)
    assert offenders == []
