"""``untaped plugin new NAME --fills OWNER.CONTRACT``: scaffold a plugin that fills a contract.

The package it writes composes and passes ``check_conventions`` as written:

- ``pyproject.toml``: ``untaped`` in this major's range, the owner as an
  extra named like it (in the installed owner's major), and the entry point
  naming ``SPEC``;
- ``src/untaped_<name>/``: ``SPEC`` offering the provider to the owner (a
  local import, so runs that ask no contract never load it), ``errors.py``,
  an empty ``settings.py`` and ``adapters/<owner>.py``. The provider there has every
  method of the contract with its docstring: required ones as stubs, the
  others written commented out, since a stub would count as filling them;
- ``tests/``: ``check_conventions`` and ``assert_fills`` with a list of
  samples to fill in.

The provider's own record type ``T`` starts as the owner's model, so no
bridge is needed until the plugin issues records of its own.
"""

from __future__ import annotations

import builtins
import inspect
import keyword
import re
from annotationlib import Format
from collections.abc import Callable, Sequence
from importlib import import_module
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path
from textwrap import dedent, indent
from typing import TYPE_CHECKING, ClassVar

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from untaped.errors import ConfigError, UsageError
from untaped.messages import not_found
from untaped.plugins.registry import (
    CompositionResult,
    PluginCandidate,
    check_plugin_name,
    owns_contracts,
    range_text,
    reserved_as,
)
from untaped.records import OutcomeRecord, TargetRecord

if TYPE_CHECKING:
    from types import ModuleType

    from untaped.contracts._declare import ContractInfo

#: The build backend of every package in this repository, and of the scaffold.
BUILD_SYSTEM = 'requires = ["uv_build>=0.11.8,<0.12.0"]\nbuild-backend = "uv_build"'


class ScaffoldOutcome(OutcomeRecord, TargetRecord, kind="untaped.scaffold_outcome"):
    """One file ``plugin new`` wrote (``created``) or would write (``planned``)."""

    table_columns: ClassVar[tuple[str, ...]] = ("action", "target_path")


def scaffold(
    result: CompositionResult,
    candidates: Sequence[PluginCandidate],
    name: str,
    fills: str,
    *,
    path: Path,
    dry_run: bool,
) -> list[ScaffoldOutcome]:
    """Write (or, with ``dry_run``, plan) the package ``untaped-<name>`` under ``path``."""
    _check_name(name, candidates)
    owner, contract = _owner_contract(result, candidates, fills)
    dest = (path / f"untaped-{name}").absolute()
    if dest.exists():
        raise UsageError(f"{dest} already exists; choose another --path or plugin name")
    files = _Scaffold(name, owner, contract, candidates).files()
    outcomes: list[ScaffoldOutcome] = []
    for relative, text in files.items():
        target = dest / relative
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        outcomes.append(
            ScaffoldOutcome(action="planned" if dry_run else "created", target_path=target)
        )
    return outcomes


def _check_name(name: str, candidates: Sequence[PluginCandidate]) -> None:
    try:
        check_plugin_name(name)
    except ConfigError as exc:
        raise UsageError(str(exc)) from None
    reserved = reserved_as(name)
    if reserved is not None:
        raise UsageError(f"{name!r} is a reserved {reserved}; choose another plugin name")
    if any(candidate.name == name for candidate in candidates):
        raise UsageError(f"a plugin named {name!r} is already installed")


def _owner_contract(
    result: CompositionResult, candidates: Sequence[PluginCandidate], fills: str
) -> tuple[PluginCandidate, ContractInfo]:
    from untaped.contracts._registry import owned_contracts  # noqa: PLC0415 - loads contracts

    owner_name, dot, contract_name = fills.partition(".")
    if not dot or not owner_name or not contract_name:
        raise UsageError(f"--fills takes OWNER.CONTRACT, such as shelf.book_source; got {fills!r}")
    owners = sorted(p.spec.name for p in result.plugins if owns_contracts(p.spec))
    candidate = next((c for c in candidates if c.name == owner_name), None)
    if owner_name not in owners or candidate is None:
        raise UsageError(not_found("contract owner", owner_name, known=owners))
    declared = owned_contracts().get(owner_name, [])
    info = next((each for each in declared if each.name == contract_name), None)
    if info is None:
        known = [f"{owner_name}.{each.name}" for each in declared]
        raise UsageError(not_found("contract", fills, known=known))
    return candidate, info


class _Scaffold:
    """The files of one scaffold, by path relative to the package directory."""

    def __init__(
        self,
        name: str,
        owner: PluginCandidate,
        info: ContractInfo,
        candidates: Sequence[PluginCandidate],
    ) -> None:
        self.name = name
        self.package = f"untaped_{name.replace('-', '_')}"
        self.camel = "".join(part.capitalize() for part in name.split("-"))
        self.owner = owner
        self.owner_module = owner.name.replace("-", "_")
        self.info = info
        self.provider = f"{self.camel}{info.cls.__name__}"
        self.api = _api(owner, candidates, info)

    def files(self) -> dict[str, str]:
        src = f"src/{self.package}"
        provider = self._provider_module()
        return {
            "pyproject.toml": self._pyproject(),
            "README.md": self._readme(),
            f"{src}/__init__.py": self._init(),
            f"{src}/errors.py": self._errors(),
            f"{src}/settings.py": self._settings(),
            f"{src}/py.typed": "",
            f"{src}/adapters/__init__.py": "",
            f"{src}/adapters/{self.owner_module}.py": provider,
            "tests/conftest.py": _CONFTEST,
            f"tests/test_{self.package.removeprefix('untaped_')}.py": self._test(),
        }

    def _pyproject(self) -> str:
        core = _core_metadata()
        owner_range = _range(self.owner.distribution_version)
        owner_dist = canonicalize_name(self.owner.distribution)
        return (
            dedent(
                f"""\
                [project]
                name = "untaped-{self.name}"
                version = "0.1.0"
                description = "{self._about()}"
                readme = "README.md"
                requires-python = "{core.python}"
                dependencies = ["untaped{core.untaped}", "{core.pydantic}"]

                [project.optional-dependencies]
                {_toml_key(self.owner.name)} = ["{owner_dist}{owner_range}"]

                [project.entry-points."untaped.plugins"]
                {_toml_key(self.name)} = "{self.package}:SPEC"

                [dependency-groups]
                dev = ["pytest>=9"]

                [build-system]
                """
            )
            + BUILD_SYSTEM
            + "\n"
        )

    def _contract(self) -> str:
        return f"``{self.owner.name}``'s {self.info.cls.__name__} contract"

    def _about(self) -> str:
        return f"An untaped plugin filling {self.owner.name}'s {self.info.cls.__name__} contract."

    def _readme(self) -> str:
        return dedent(
            f"""\
            # untaped-{self.name}

            An [untaped](https://github.com/alexisbeaulieu97/untaped) plugin filling
            `{self.owner.name}`'s `{self.info.cls.__name__}` contract. Install it with its owner:

            ```sh
            uv pip install -e '.[{self.owner.name}]'
            ```

            Fill the methods in `src/{self.package}/adapters/{self.owner_module}.py`, add
            samples to `tests/test_{self.package.removeprefix("untaped_")}.py`, then run
            `pytest` and `untaped plugin check {self.name}`.
            """
        )

    def _init(self) -> str:
        function = f"_{self.owner_module}"
        adapter = f"{self.package}.adapters.{self.owner_module}"
        local = f"from {adapter} import {self.provider}  # noqa: PLC0415"
        return dedent(
            f'''\
            """The ``{self.name}`` plugin: it fills {self._contract()}.

            ``provides`` maps the owner's name to a function returning this plugin's
            providers; its local import keeps ``untaped.contracts`` out of every run
            that asks no contract.
            """

            from __future__ import annotations

            from collections.abc import Sequence
            from typing import TYPE_CHECKING

            from untaped.sdk import PluginSpec

            from {self.package}.settings import {self.camel}Settings

            if TYPE_CHECKING:
                from untaped.contracts import Contract

            __all__ = ["SPEC"]


            def {function}() -> Sequence[Contract]:
                {local}

                return ({self.provider}(),)


            SPEC = PluginSpec(
                name="{self.name}",
                settings={self.camel}Settings,
                provides={{"{self.owner.name}": {function}}},
            )
            '''
        )

    def _errors(self) -> str:
        return dedent(
            f'''\
            """Typed exceptions for the ``{self.name}`` plugin."""

            from __future__ import annotations

            from untaped.sdk import UntapedError


            class {self.camel}Error(UntapedError):
                """Base for {self.name} plugin failures."""

                system = "{self.name}"
            '''
        )

    def _settings(self) -> str:
        return dedent(
            f'''\
            """The ``{self.name}`` config section."""

            from __future__ import annotations

            from pydantic import BaseModel, ConfigDict


            class {self.camel}Settings(BaseModel):
                """``{self.name}:`` in each profile; add the fields the provider needs."""

                model_config = ConfigDict(frozen=True)
            '''
        )

    def _provider_module(self) -> str:
        info = self.info
        param = info.item_param.__name__ if info.item_param is not None else None
        model = info.item.__name__ if info.item is not None else None
        live: list[str] = []
        commented: list[str] = []
        for method_name, method in info.methods.items():
            text = _method(method_name, method.function, param, model)
            if method.bridge:
                commented.append(
                    "# Only when this provider issues records of its own: subclass "
                    f"{info.cls.__name__}[YourRecord]\n# and turn each into the owner's.\n"
                    + _comment(text)
                )
            elif method_name in getattr(info.cls, "__abstractmethods__", ()):
                live.append(text)
            else:
                commented.append(
                    "# Optional: fill it when the API can answer it.\n" + _comment(text)
                )
        names = {info.cls.__name__, *_names("\n".join(live))}
        imports = _imports(names, self.api, self.package, f"{self.camel}Settings")
        body = "\n".join([*live, *commented]) or "pass\n"
        return (
            f'"""{self.name}\'s provider of {self.owner.name}\'s {info.cls.__name__} contract.\n\n'
            f"It imports only ``untaped.contracts``, ``untaped.sdk`` and ``{self.api.__name__}``.\n"
            '"""\n\nfrom __future__ import annotations\n\n'
            f"{imports}\n\n\n"
            f"class {self.provider}({info.cls.__name__}, Configured[{self.camel}Settings]):\n"
            f'    """Fills {info.cls.__name__} from ... (say where the answers come from)."""\n\n'
            + indent(body, "    ")
        )

    def _test(self) -> str:
        adapter = f"{self.package}.adapters.{self.owner_module}"
        short = self.package.removeprefix("untaped_")
        return dedent(
            f'''\
            """``{self.name}`` follows the conventions and fills {self.owner.name}'s contract."""

            from __future__ import annotations

            from untaped.testing import assert_fills, check_conventions

            from {adapter} import {self.provider}

            #: Records as the provider issues them (its ``T``), each checked by ``assert_fills``.
            #: Add a few, built the way your API returns them.
            SAMPLES: list[object] = []


            def test_{short}_follows_the_conventions() -> None:
                check_conventions("{self.name}")


            def test_{short}_fills_{self.info.name}() -> None:
                assert_fills({self.provider}, samples=SAMPLES)
            '''
        )


_CONFTEST = '''\
"""Every test runs in untaped's hermetic environment (isolated HOME and config)."""

pytest_plugins = ["untaped.testing.plugin"]
'''


def _api(
    owner: PluginCandidate, candidates: Sequence[PluginCandidate], info: ContractInfo
) -> ModuleType:
    """The owner's ``api`` module, which must export the contract."""
    from untaped.conventions import candidate_package  # noqa: PLC0415 - imports the root

    package = candidate_package(owner.target) or info.cls.__module__.partition(".")[0]
    module_name = f"{package.partition('.')[0]}.api"
    try:
        module = import_module(module_name)
    except ImportError:
        module = None
    if module is None or getattr(module, info.cls.__name__, None) is not info.cls:
        raise UsageError(
            f"{owner.name} doesn't export {info.cls.__name__} from {module_name}, "
            "the only module a provider may import from its owner"
        )
    return module


def _method(
    name: str, function: Callable[..., object], param: str | None, model: str | None
) -> str:
    """``def`` line, docstring and ``raise NotImplementedError`` of a contract method."""
    text = f"def {name}{_signature(function)}:"
    if param is not None and model is not None:
        text = re.sub(rf"\b{re.escape(param)}\b", model, text)
    doc = inspect.getdoc(function)
    lines = [text]
    if doc:
        quoted = doc.replace('"""', '\\"\\"\\"')
        lines.append(
            indent(f'"""{quoted}"""' if "\n" not in quoted else f'"""{quoted}\n"""', "    ")
        )
    lines.append("    raise NotImplementedError")
    return "\n".join(lines) + "\n"


def _signature(function: Callable[..., object]) -> str:
    """``(self, a: int = 1, *, b: str) -> list[Book]``, each annotation as the source writes it."""
    signature = inspect.signature(function, annotation_format=Format.STRING)
    parts: list[str] = []
    keyword_only = False
    for parameter in signature.parameters.values():
        kind = parameter.kind
        if kind is parameter.KEYWORD_ONLY and not keyword_only:
            parts.append("*")
        keyword_only = keyword_only or kind in (parameter.KEYWORD_ONLY, parameter.VAR_POSITIONAL)
        prefix = {"VAR_POSITIONAL": "*", "VAR_KEYWORD": "**"}.get(kind.name, "")
        text = f"{prefix}{parameter.name}"
        if parameter.annotation is not parameter.empty:
            text += f": {parameter.annotation}"
        if parameter.default is not parameter.empty:
            text += f" = {parameter.default!r}" if ":" in text else f"={parameter.default!r}"
        parts.append(text)
        if kind is parameter.POSITIONAL_ONLY and _last_positional(signature, parameter):
            parts.append("/")
    returns = signature.return_annotation
    arrow = "" if returns is signature.empty else f" -> {returns}"
    return f"({', '.join(parts)}){arrow}"


def _last_positional(signature: inspect.Signature, parameter: inspect.Parameter) -> bool:
    names = [p.name for p in signature.parameters.values() if p.kind is p.POSITIONAL_ONLY]
    return names[-1] == parameter.name


def _comment(text: str) -> str:
    return "".join(
        f"# {line}" if line.strip() else "#\n" for line in text.splitlines(keepends=True)
    )


def _names(code: str) -> set[str]:
    found = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", code))
    return {
        name
        for name in found
        if not keyword.iskeyword(name) and not hasattr(builtins, name) and name != "self"
    } - {"def", "NotImplementedError"}


def _imports(names: set[str], api: ModuleType, package: str, settings: str) -> str:
    """The provider module's imports: what it names from the owner's api, else from where it lives."""
    found = vars(api)
    owned: list[str] = []
    others: dict[str, list[str]] = {}
    for name in sorted(names):
        value = found.get(name)
        if value is None:
            continue
        module = getattr(value, "__module__", None) or ""
        if not module or module.startswith(("untaped", api.__name__.partition(".")[0])):
            owned.append(name)
        else:
            others.setdefault(module, []).append(name)
    third = sorted([f"from {api.__name__} import {', '.join(owned)}", _CONFIGURED])
    groups = [
        "\n".join(
            f"from {module} import {', '.join(each)}" for module, each in sorted(others.items())
        ),
        "\n".join(third),
        f"from {package}.settings import {settings}",
    ]
    return "\n\n".join(group for group in groups if group)


_CONFIGURED = "from untaped.contracts import Configured"


class _Core:
    def __init__(self, untaped: str, python: str, pydantic: str) -> None:
        self.untaped = untaped
        self.python = python
        self.pydantic = pydantic


def _core_metadata() -> _Core:
    """This untaped's major range, its Python requirement and its pydantic requirement."""
    try:
        found = metadata("untaped")
    except PackageNotFoundError:
        return _Core(_range(""), ">=3.14.1", "pydantic>=2,<3")
    pydantic = "pydantic>=2,<3"
    for line in found.get_all("Requires-Dist") or []:
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            continue
        if requirement.name == "pydantic" and requirement.marker is None:
            pydantic = f"pydantic{range_text(requirement)}"
    return _Core(
        _range(found.get("Version") or ""), found.get("Requires-Python") or ">=3.14.1", pydantic
    )


def _range(version: str) -> str:
    """``>=M,<M+1`` for an installed version (``>=0.m,<1`` before 1.0); ``""`` when unknown."""
    try:
        parsed = Version(version)
    except InvalidVersion:
        return ""
    if parsed.major == 0:
        return f">=0.{parsed.minor},<1"
    return f">={parsed.major},<{parsed.major + 1}"


def _toml_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else f'"{name}"'
