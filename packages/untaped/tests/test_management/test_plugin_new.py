"""``untaped plugin new``: a scaffold that composes, follows the conventions, fills its contract."""

from __future__ import annotations

import importlib
import json
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from test_management.contract_plugins import candidates
from untaped import bootstrap
from untaped.conventions import plugin_violations
from untaped.errors import UsageError
from untaped.management.plugin_new import BUILD_SYSTEM, ScaffoldOutcome, scaffold
from untaped.plugins.registry import PluginCandidate
from untaped.testing import assert_fills, compose_with, invoke_cli

pytestmark = pytest.mark.usefixtures(
    "fresh_composition", "_isolated_config", "contract_plugins_site"
)

_REPO = Path(__file__).resolve().parents[4]
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


def test_the_scaffold_composes_follows_the_conventions_and_fills_its_contract(
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
    assert project["dependencies"][0] == "untaped>=10,<11"
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
    assert plugin_violations("gitlab", candidates=[_RACK, gitlab]) == []
    provider = importlib.import_module("untaped_gitlab.providers.rack").GitlabItemSource
    with compose_with(_RACK, gitlab):
        assert_fills(provider)
    assert (root / "src" / "untaped_gitlab" / "fills.json").exists()


def test_the_provider_carries_each_method_and_docstring(tmp_path: Path) -> None:
    _new(tmp_path)
    provider = (tmp_path / "untaped-gitlab/src/untaped_gitlab/providers/rack.py").read_text(
        encoding="utf-8"
    )
    # date comes from where the contract's module found it, never from rack's api.
    assert "from datetime import date\n\nfrom untaped.contracts import Configured\n" in provider
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
        ("rack", "rack.item_source", "a plugin named 'rack' is already installed"),
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


def test_an_existing_directory_is_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "untaped-gitlab").mkdir()
    with pytest.raises(UsageError, match="already exists"):
        _new(tmp_path)
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
    from untaped.management.plugin_new import _range

    assert _range(version) == expected


def test_an_owner_without_its_contract_in_api_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.contracts._registry import owned_contracts

    result = bootstrap.compose_root(candidates=[_RACK])
    assert owned_contracts()["rack"]  # loaded before the api stops exporting it
    monkeypatch.delattr(importlib.import_module("untaped_rack.api"), "ItemSource")
    with pytest.raises(UsageError, match=r"rack doesn't export ItemSource from untaped_rack\.api"):
        scaffold(result, [_RACK], "gitlab", "rack.item_source", path=tmp_path, dry_run=True)


def test_a_name_the_owners_api_does_not_export_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.contracts._registry import owned_contracts

    result = bootstrap.compose_root(candidates=[_RACK])
    assert owned_contracts()["rack"]
    monkeypatch.delattr(importlib.import_module("untaped_rack.api"), "Item")
    with pytest.raises(UsageError, match=r"ItemSource names Item, which untaped_rack\.api doesn't"):
        scaffold(result, [_RACK], "gitlab", "rack.item_source", path=tmp_path, dry_run=True)


def _toy_imports(
    monkeypatch: pytest.MonkeyPatch, names: set[str], **contract_globals: object
) -> str:
    """``_imports`` for a contract in ``untaped_toy._contract`` with ``api`` exporting Item."""
    from untaped.management.plugin_new import _imports

    contract_module = ModuleType("untaped_toy._contract")
    api = ModuleType("untaped_toy.api")

    class Item:
        pass

    class Source:
        pass

    Source.__module__ = contract_module.__name__
    vars(contract_module).update(contract_globals, Item=Item, Source=Source)
    api.Item = Item  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, contract_module.__name__, contract_module)
    return _imports(names, api, Source, "untaped_gitlab", "GitlabSettings")


def test_a_module_the_contract_imports_is_imported_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt

    found = _toy_imports(monkeypatch, {"Item", "dt", "datetime"}, dt=dt, datetime=dt)
    assert found.splitlines()[:2] == ["import datetime", "import datetime as dt"]


def test_an_untaped_name_comes_from_its_public_module(monkeypatch: pytest.MonkeyPatch) -> None:
    from untaped.contracts import NotReady

    found = _toy_imports(monkeypatch, {"NotReady"}, NotReady=NotReady)
    assert found.splitlines()[0] == "from untaped.contracts import NotReady"


@pytest.mark.parametrize(
    "value",
    [
        dict[str, int] | None,
        type("Hidden", (), {"__module__": "untaped_toy._contract"}),
        ModuleType("untaped_toy.helpers"),
    ],
    ids=["alias", "owner-private", "owner-module"],
)
def test_a_name_without_a_public_home_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    with pytest.raises(UsageError, match=r"Source names Payload, which untaped_toy\.api doesn't"):
        _toy_imports(monkeypatch, {"Payload"}, Payload=value)


def test_a_local_version_is_left_out_of_the_range() -> None:
    from untaped.management.plugin_new import _range

    assert _range("11.0.0a1+g12ab") == ">=11.0.0a1,<12"
