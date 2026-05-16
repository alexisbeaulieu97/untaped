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
  ``packages/*/src/*/infrastructure/`` passes ``verify=`` (so TLS
  defaults flow through ``resolve_verify(settings.http)``).
- :func:`test_typer_apps_and_required_arg_commands_set_no_args_is_help` —
  Hard Rule #9. Every ``typer.Typer`` app and every command with at
  least one required argument sets ``no_args_is_help=True`` (so
  no-args invocation shows help instead of erroring).

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

import typer
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
    offenders: list[str] = []
    for descriptor in walk_settings(Settings):
        leaf_name = descriptor.path[-1].lower()
        if not _CREDENTIAL_NAME_RE.search(leaf_name):
            continue
        # ``is_secret`` is the canonical "is this a credential" predicate
        # on the descriptor. Equivalent to ``annotation is SecretStr``
        # today, but a future widening of "what counts as a secret"
        # (e.g. detecting secrets inside Union types properly) would
        # land in ``is_secret``'s computation, and every caller would
        # pick it up for free.
        if not descriptor.is_secret:
            offenders.append(f"{descriptor.key} :: {descriptor.annotation!r}")
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


def test_httpclient_construction_passes_verify() -> None:
    """Every ``HttpClient(...)`` under ``infrastructure/`` must pass ``verify=``.

    Hard Rule #12. AST walk over every domain's ``infrastructure/`` tree
    so the check survives reformatting and ignores ``# verify=`` style
    comments that a string-match regex would be fooled by. A new client
    that forgets ``verify=resolve_verify(...)`` fails here with the
    ``file:line`` of the offending call.

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
            for call in _httpclient_calls_in(tree):
                kwarg_names = {kw.arg for kw in call.keywords if kw.arg}
                if "verify" not in kwarg_names:
                    rel = py_file.relative_to(REPO_ROOT)
                    offenders.append(f"{rel}:{call.lineno}")
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


def test_typer_apps_and_required_arg_commands_set_no_args_is_help() -> None:
    """Every Typer app and every required-arg command sets ``no_args_is_help=True``.

    Hard Rule #9. Imports the live root app (which pulls every domain
    via :mod:`untaped.main`), walks its groups + commands, and reports
    any offender with its qualified command path.
    """
    from untaped.main import app as root_app

    # Seed the walk with the root app so the loop handles every entry
    # uniformly — no special-case for the root before the loop.
    offenders: list[str] = []
    for name, item in [("<root>", root_app), *_walk_typer(root_app)]:
        if isinstance(item, typer.Typer):
            if not item.info.no_args_is_help:
                offenders.append(f"app: {name}")
            continue
        if _has_required_arg(item.callback) and not item.no_args_is_help:
            offenders.append(f"command: {name}")
    assert not offenders, (
        "Typer apps and required-arg commands must set no_args_is_help=True "
        "(see AGENTS.md Hard Rule #9):\n  " + "\n  ".join(offenders)
    )
