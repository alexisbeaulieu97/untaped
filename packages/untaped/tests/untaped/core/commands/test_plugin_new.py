"""``untaped plugin new``: a scaffold that composes, follows the conventions, fills its contract."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import tomllib
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path
from types import ModuleType

import pytest

from core_test_support.commands.contract_plugins import CORE_RANGE, candidates
from untaped.core.commands.plugin_new import BUILD_SYSTEM, ScaffoldOutcome, _range, scaffold
from untaped.core.plugin_check.rules import plugin_violations
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.errors import ConfigError, UntapedError, UsageError
from untaped.sdk.testing import compose_with, invoke_cli

pytestmark = pytest.mark.usefixtures(
    "fresh_composition", "_isolated_config", "contract_plugins_site"
)

_REPO = Path(__file__).resolve().parents[6]
_RACK = next(candidate for candidate in candidates() if candidate.name == "rack")


@pytest.fixture(autouse=True)
def _forget_the_scaffold() -> Iterator[None]:
    """Unload a scaffold a test imported, so the next test's is its own."""
    yield
    for module in [m for m in sys.modules if m.split(".")[0] == "untaped_gitlab"]:
        del sys.modules[module]


def _new(
    tmp_path: Path, name: str = "gitlab", fills: str = "rack.item_source", *, dry_run: bool = False
) -> list[ScaffoldOutcome]:
    result = bootstrap.compose_root(candidates=[_RACK])
    return scaffold(result, [_RACK], name, fills, path=tmp_path, dry_run=dry_run)


def _requires(pyproject: dict[str, object]) -> tuple[str, ...]:
    """``Requires-Dist`` as the built wheel would list it."""
    project = pyproject["project"]
    assert isinstance(project, dict)
    found = list(project["dependencies"])
    for extra, requirements in project["optional-dependencies"].items():
        found += [f"{requirement}; extra == '{extra}'" for requirement in requirements]
    return tuple(found)


def test_the_scaffold_composes_passes_lint_and_fills_its_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes = _new(tmp_path)
    root = tmp_path / "untaped-gitlab"
    assert {outcome.action for outcome in outcomes} == {"created"}
    assert sorted(str(outcome.target_path.relative_to(root)) for outcome in outcomes) == [
        "README.md",
        "pyproject.toml",
        "src/untaped_gitlab/__init__.py",
        "src/untaped_gitlab/errors.py",
        "src/untaped_gitlab/providers/__init__.py",
        "src/untaped_gitlab/providers/rack.py",
        "src/untaped_gitlab/py.typed",
        "src/untaped_gitlab/settings.py",
        "tests/conftest.py",
        "tests/test_gitlab.py",
    ]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    assert project["dependencies"][0] == CORE_RANGE
    assert project["optional-dependencies"] == {"rack": ["untaped-rack>=1,<2"]}
    assert project["entry-points"]["untaped.plugins"] == {"gitlab": "untaped_gitlab:SPEC"}

    monkeypatch.syspath_prepend(str(root / "src"))
    importlib.invalidate_caches()
    gitlab = PluginCandidate(
        distribution="untaped-gitlab",
        name="gitlab",
        target="untaped_gitlab:SPEC",
        distribution_version="0.1.0",
        requires_dist=_requires(pyproject),
    )
    lint_range = _range(metadata.version("untaped"), minor=True)  # lint came in a minor
    assert pyproject["dependency-groups"] == {"dev": ["pytest>=9", f"untaped[lint]{lint_range}"]}
    assert plugin_violations("gitlab", candidates=[_RACK, gitlab]) == []
    # Its own tests import untaped[lint]; core's own run (no plugin installed) stops here.
    lint = pytest.importorskip("untaped_lint.testing")
    # Its lint test, with the candidates it can't discover; then its other tests.
    lint.assert_clean("gitlab", ignore=["lint.live"], candidates=[_RACK, gitlab])
    assert 'assert_clean("gitlab", ignore=["lint.live"])' in (
        root / "tests" / "test_gitlab.py"
    ).read_text(encoding="utf-8")
    tests = _load(root / "tests" / "test_gitlab.py")
    with compose_with(_RACK, gitlab):
        tests.test_gitlab_fills_item_source()
    assert (root / "src" / "untaped_gitlab" / "fills.json").exists()
    tests.test_gitlab_keeps_its_surface()
    # No commands, settings or modules yet: the first run writes an empty surface.
    surface = (root / "tests" / "surface.txt").read_text(encoding="utf-8")
    assert surface == "# untaped surface 1\n"
    tests.test_gitlab_keeps_its_surface()


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"scaffold_{path.stem}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_provider_carries_each_method_and_docstring(tmp_path: Path) -> None:
    _new(tmp_path)
    provider = (tmp_path / "untaped-gitlab/src/untaped_gitlab/providers/rack.py").read_text(
        encoding="utf-8"
    )
    # date comes from where the contract's module found it, never from rack's api.
    assert "from datetime import date\n\nfrom untaped.sdk.contracts import Configured\n" in provider
    assert "from untaped_rack.api import Item, ItemSource\n" in provider
    assert "class GitlabItemSource(ItemSource, Configured[GitlabSettings]):" in provider
    assert (
        "    def items(self, since: date | None = None) -> list[Item]:\n"
        '        """Every item on the rack."""\n'
        "        raise NotImplementedError\n"
    ) in provider
    # An optional method or a bridge is written commented out: a stub would fill it, and T
    # starts as the owner's model.
    assert (
        "    # def named(self, name: str, *, limit: int = 10) -> list[Item]:\n"
        '    #     """The items called ``name``."""\n'
    ) in provider
    assert "    # def to_item(self, item: Item) -> Item:\n" in provider
    compile(provider, "rack.py", "exec")


def test_dry_run_plans_every_file_and_writes_none(tmp_path: Path) -> None:
    outcomes = _new(tmp_path, dry_run=True)
    assert {outcome.action for outcome in outcomes} == {"planned"}
    assert len(outcomes) == 10
    assert not (tmp_path / "untaped-gitlab").exists()


@pytest.mark.parametrize(
    ("name", "fills", "message"),
    [
        ("Git_Lab", "rack.item_source", "must be lowercase words"),
        ("plugin", "rack.item_source", "'plugin' is a reserved"),
        ("gitlab", "rack", "--fills takes OWNER.CONTRACT"),
        ("gitlab", "nowhere.item_source", "contract owner not found: 'nowhere'; known: rack"),
        ("gitlab", "rack.nothing", "contract not found: 'rack.nothing'; known: rack.item_source"),
    ],
)
def test_what_it_cannot_scaffold_is_a_usage_error(
    tmp_path: Path, name: str, fills: str, message: str
) -> None:
    with pytest.raises(UsageError, match=message.replace(".", r"\.")):
        _new(tmp_path / "out", name, fills)
    assert not (tmp_path / "out").exists()


def test_an_installed_name_is_a_conflict(tmp_path: Path) -> None:
    with pytest.raises(UntapedError, match="a plugin named 'rack' is already installed") as raised:
        _new(tmp_path / "out", "rack")
    assert raised.value.category == "conflict"
    assert not (tmp_path / "out").exists()


def test_an_existing_directory_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "untaped-gitlab").mkdir()
    with pytest.raises(UntapedError, match="already exists") as raised:
        _new(tmp_path)
    assert raised.value.category == "conflict"
    assert list((tmp_path / "untaped-gitlab").iterdir()) == []


def test_the_command_emits_scaffold_outcomes_and_exits_2_on_a_usage_error(tmp_path: Path) -> None:
    app = bootstrap.build_root_app(candidates=[_RACK]).meta
    args = ["plugin", "new", "gitlab", "--fills", "rack.item_source", "--path", str(tmp_path)]
    planned = invoke_cli(app, [*args, "--dry-run", "--format", "json"])
    assert planned.exit_code == 0, planned.output
    rows = json.loads(planned.stdout)
    assert rows[0] == {
        "action": "planned",
        "target_path": str(tmp_path / "untaped-gitlab" / "pyproject.toml"),
    }
    refused = invoke_cli(app, ["plugin", "new", "gitlab", "--fills", "rack"])
    assert refused.exit_code == 2


def test_the_scaffold_builds_like_the_repository_packages() -> None:
    core = tomllib.loads((_REPO / "packages" / "untaped" / "pyproject.toml").read_text("utf-8"))
    assert tomllib.loads(f"[build-system]\n{BUILD_SYSTEM}")["build-system"] == core["build-system"]


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("10.1.0", ">=10,<11"),
        ("0.3.1", ">=0.3,<0.4"),
        ("11.0.0a1", ">=11.0.0a1,<12"),
        ("", ""),
    ],
)
def test_an_owner_range_covers_its_installed_major(version: str, expected: str) -> None:
    from untaped.core.commands.plugin_new import _range

    assert _range(version) == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [("11.1.0", ">=11.1,<12"), ("0.3.1", ">=0.3,<0.4"), ("11.1.0a1", ">=11.1.0a1,<12")],
)
def test_a_minor_range_floors_at_the_installed_minor(version: str, expected: str) -> None:
    from untaped.core.commands.plugin_new import _range

    assert _range(version, minor=True) == expected


def test_an_owner_without_its_contract_in_api_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.runtime.contracts.registry import owned_contracts

    result = bootstrap.compose_root(candidates=[_RACK])
    assert owned_contracts()["rack"]  # loaded before the api stops exporting it
    monkeypatch.delattr(importlib.import_module("untaped_rack.api"), "ItemSource")
    with pytest.raises(
        ConfigError, match=r"rack doesn't export ItemSource from untaped_rack\.api"
    ) as raised:
        scaffold(result, [_RACK], "gitlab", "rack.item_source", path=tmp_path, dry_run=True)
    assert (raised.value.system, raised.value.hint) == ("rack", "upgrade untaped-rack")


def test_a_name_the_owners_api_does_not_export_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.runtime.contracts.registry import owned_contracts

    result = bootstrap.compose_root(candidates=[_RACK])
    assert owned_contracts()["rack"]
    monkeypatch.delattr(importlib.import_module("untaped_rack.api"), "Item")
    with pytest.raises(
        ConfigError, match=r"ItemSource names Item, which untaped_rack\.api doesn't"
    ) as raised:
        scaffold(result, [_RACK], "gitlab", "rack.item_source", path=tmp_path, dry_run=True)
    assert raised.value.system == "rack"


def _toy_imports(
    monkeypatch: pytest.MonkeyPatch,
    names: set[str],
    exports: dict[str, object] | None = None,
    **contract_globals: object,
) -> str:
    """``_imports`` for a contract in ``untaped_toy._contract`` with ``api`` exporting Item."""
    from untaped.core.commands.plugin_new import _imports

    contract_module = ModuleType("untaped_toy._contract")
    api = ModuleType("untaped_toy.api")

    class Item:
        pass

    class Source:
        pass

    Source.__module__ = contract_module.__name__
    vars(contract_module).update(contract_globals, Item=Item, Source=Source)
    vars(api).update(exports or {}, Item=Item)
    api.__all__ = sorted({*(exports or {}), "Item"})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, contract_module.__name__, contract_module)
    return _imports(names, api, Source, "untaped_gitlab", "GitlabSettings", owner="toy")


def test_a_module_the_contract_imports_is_imported_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt

    found = _toy_imports(monkeypatch, {"Item", "dt", "datetime"}, dt=dt, datetime=dt)
    assert found.splitlines()[:2] == ["import datetime", "import datetime as dt"]


def test_untaped_names_come_from_the_modules_a_plugin_may_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from untaped.sdk import TargetRecord, UtcTimestamp
    from untaped.sdk.contracts import NotReady

    found = _toy_imports(
        monkeypatch,
        {"Item", "NotReady", "TargetRecord", "UtcTimestamp", "Path"},
        NotReady=NotReady,
        TargetRecord=TargetRecord,
        UtcTimestamp=UtcTimestamp,
        Path=Path,
    )
    assert found == (
        "from pathlib import Path\n\n"
        "from untaped.sdk import TargetRecord, UtcTimestamp\n"
        "from untaped.sdk.contracts import Configured, NotReady\n"
        "from untaped_toy.api import Item\n\n"
        "from untaped_gitlab.settings import GitlabSettings"
    )


def test_a_module_the_owners_api_exports_comes_from_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = ModuleType("untaped_toy.models")
    found = _toy_imports(monkeypatch, {"models"}, {"models": models}, models=models)
    assert found.splitlines()[1] == "from untaped_toy.api import models"


def test_a_name_the_api_keeps_for_itself_comes_from_its_own_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decimal import Decimal

    found = _toy_imports(monkeypatch, {"Item", "Decimal"}, Decimal=Decimal)
    assert found.splitlines()[0] == "from decimal import Decimal"


def test_a_name_is_placed_without_pytest_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A tool install has no pytest, which untaped.sdk.testing.git imports at the top.
    from decimal import Decimal

    from untaped.core.commands.plugin_new import _home

    monkeypatch.delitem(sys.modules, "untaped.sdk.testing.git", raising=False)
    monkeypatch.setitem(sys.modules, "pytest", None)
    assert _home("Decimal", Decimal) == "decimal"


def test_an_attribute_after_a_dot_comes_with_its_module() -> None:
    from untaped.core.commands.plugin_new import _names

    assert _names("def f(self, where: pathlib.Path) -> None:") == {"f", "where", "pathlib"}


@pytest.mark.parametrize(
    "value",
    [
        dict[str, int] | None,
        type("Hidden", (), {"__module__": "untaped_toy._contract"}),
        type("Lost", (), {"__module__": "nowhere_at_all.sub"}),
        type("Inside", (), {"__module__": "untaped.records"}),
        ModuleType("untaped_toy.helpers"),
        ModuleType("untaped.sdk.contracts._declare"),
    ],
    ids=["alias", "owner-private", "unimportable", "untaped-internal", "owner-module", "private"],
)
def test_a_name_without_a_public_home_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    with pytest.raises(ConfigError, match=r"Source names Payload, which untaped_toy\.api doesn't"):
        _toy_imports(monkeypatch, {"Payload"}, Payload=value)


def test_a_docstring_word_imports_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap.compose_root(candidates=[_RACK])
    contract = importlib.import_module("untaped_rack._contract")
    # ``items``'s docstring says "rack", a name with no public home in rack's module.
    monkeypatch.setattr(contract, "rack", object(), raising=False)
    _new(tmp_path)
    provider = (tmp_path / "untaped-gitlab/src/untaped_gitlab/providers/rack.py").read_text(
        encoding="utf-8"
    )
    assert "import rack" not in provider


def test_a_local_version_is_left_out_of_the_range() -> None:
    from untaped.core.commands.plugin_new import _range

    assert _range("11.0.0a1+g12ab") == ">=11.0.0a1,<12"
