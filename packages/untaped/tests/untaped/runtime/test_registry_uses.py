"""Composition looks up what each plugin imports and fills before the plugin's own import.

The plugins are real installs (``core_test_support.runtime.plugin_uses_site``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path
from textwrap import dedent

import pytest

import untaped.runtime.registry as registry
from core_test_support.runtime.plugin_uses_site import FILLS_BOOKS, OTHER, Site, installed_site
from untaped.runtime import bootstrap
from untaped.runtime.declaration import UseDecl
from untaped.runtime.registry import CompositionResult, PluginCandidate, compose
from untaped.sdk.marks import forget_notices
from untaped.sdk.storage import installed_code as installed_code_module
from untaped.sdk.testing import CliInvoker

_UNTAPED = version("untaped")


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    with installed_site(tmp_path, monkeypatch) as installed:
        yield installed


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The entry modules whose imports composition read from source."""
    read: list[str] = []
    real = registry.read_imports

    def counted(module: str, package: str | None = None):  # type: ignore[no-untyped-def]
        read.append(module)
        return real(module, package)

    monkeypatch.setattr(registry, "read_imports", counted)
    return read


def _quarantined(result: CompositionResult) -> dict[str, tuple[str, str, str | None]]:
    return {each.name: (each.reason, each.detail, each.fix) for each in result.quarantine}


def _composed(result: CompositionResult) -> list[str]:
    return [plugin.name for plugin in result.plugins]


# --- a missing name -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("floor", "words", "fix"),
    [
        (
            "untaped-shelf>=1.0",
            "untaped-acme is out of date (built for untaped-shelf 1.0)",
            "upgrade untaped-acme, or remove it",
        ),
        (
            "untaped-shelf>=3.0",
            "untaped-acme may need a newer untaped-shelf than it declares",
            "upgrade untaped-shelf",
        ),
    ],
    ids=["a-major-since-its-floor", "none-since"],
)
def test_a_missing_name_disables_only_that_plugin_by_its_floor(
    site: Site, floor: str, words: str, fix: str
) -> None:
    acme = site.acme("from untaped_shelf.api import gone\n", requires=(floor,))
    result = compose([acme, site.shelf()])
    assert _composed(result) == ["untaped", "shelf"]
    assert _quarantined(result) == {
        "acme": (
            "uses-missing",
            "untaped-acme 1.0: untaped_acme/report.py:1 imports untaped_shelf.api.gone, which "
            f"untaped-shelf 3.0 doesn't have: {words}; {fix}",
            fix,
        )
    }


def test_a_missing_name_of_untaped_without_a_requirement_names_both_fixes(site: Site) -> None:
    acme = site.acme(
        "import json\nfrom untaped.sdk import gone, plural\nfrom untaped.sdk import lost\n"
    )
    [(reason, detail, fix)] = _quarantined(compose([acme])).values()
    assert reason == "uses-missing"
    assert detail == (
        "untaped-acme 1.0: untaped_acme/report.py:2 imports untaped.sdk.gone, which "
        f"untaped {_UNTAPED} doesn't have; untaped-acme declares no requirement on untaped "
        "(and 1 more missing); "
        "upgrade untaped, or upgrade untaped-acme"
    )
    assert fix == "upgrade untaped, or upgrade untaped-acme"


def test_a_guarded_missing_name_changes_nothing(site: Site) -> None:
    acme = site.acme(
        """
        try:
            from untaped_shelf.api import gone
        except ImportError:
            gone = None
        """,
        requires=("untaped-shelf>=1.0",),
    )
    result = compose([acme, site.shelf()])
    assert result.quarantine == ()
    assert _composed(result) == ["untaped", "acme", "shelf"]


def test_a_module_that_raises_disables_the_plugin_importing_it(site: Site) -> None:
    acme = site.acme("from untaped_shelf.broken import nothing\n", requires=("untaped-shelf>=1.0",))
    reason, detail, fix = _quarantined(compose([acme, site.shelf()]))["acme"]
    assert (reason, fix) == ("import-failed", "fix untaped-shelf first")
    assert detail == (
        "untaped-acme 1.0: untaped_acme/report.py:1 imports untaped_shelf.broken.nothing, and "
        "importing it raised RuntimeError: shelf is half installed; fix untaped-shelf first"
    )


# --- a deprecated name ------------------------------------------------------------------------


def _uses(result: CompositionResult) -> dict[str, tuple[UseDecl, ...]]:
    return {plugin.name: plugin.uses for plugin in result.mount.declaration.plugins if plugin.uses}


def test_a_deprecated_import_is_kept_on_the_plugin_s_declaration(site: Site) -> None:
    acme = site.acme(
        "from untaped_shelf.api import human_size, size_text\n", requires=("untaped-shelf>=1.0",)
    )
    result = compose([acme, site.shelf()])
    use = UseDecl(
        id="sdk untaped_shelf.api.human_size",
        how="imports",
        distribution="untaped-shelf",
        removed_in=5,
        replacement="`size_text`",
        where="untaped_acme/report.py:1",
    )
    assert _uses(result) == {"acme": (use,)}
    assert result.found_uses == (("acme", use),)
    again = compose([acme, site.shelf()])
    assert _uses(again) == {"acme": (use,)}  # read from the kept declaration
    assert again.found_uses == ()  # found nothing new: nothing warns


def test_a_deprecated_contract_method_a_provider_fills_is_a_use(site: Site) -> None:
    acme = site.acme(FILLS_BOOKS, requires=("untaped-shelf>=1.0",))
    result = compose([acme, site.shelf()])
    assert _uses(result) == {
        "acme": (
            UseDecl(
                id="contract shelf.book_source.books",
                how="fills",
                distribution="untaped-shelf",
                removed_in=5,
                replacement="`BookSource.all_books`",
                where="untaped_acme.report:Shelved.books",
            ),
        )
    }


def test_a_method_the_contract_no_longer_has_is_no_use(site: Site) -> None:
    acme = site.acme(
        FILLS_BOOKS.replace("def books(", "def gone_books("), requires=("untaped-shelf>=1.0",)
    )
    result = compose([acme, site.shelf()])
    assert result.quarantine == ()
    assert _uses(result) == {}


def test_a_deprecated_import_warns_once_on_the_composing_run(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    acme = site.acme("from untaped_shelf.api import human_size\n", requires=("untaped-shelf>=1.0",))
    found = [acme, site.shelf()]
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")

    def notices() -> list[dict[str, object]]:
        result = CliInvoker().invoke(bootstrap.build_root_app(candidates=found).meta, ["--version"])
        assert result.exit_code == 0, result.output
        lines = [json.loads(line) for line in result.stderr.splitlines()]
        return [line for line in lines if line["kind"] == "untaped.deprecation"]

    assert notices() == [
        {
            "level": "warning",
            "kind": "untaped.deprecation",
            "id": "sdk untaped_shelf.api.human_size",
            "removed_in": 5,
            "distribution": "untaped-shelf",
            "replacement": "size_text",
            "hint": "it keeps working until then; upgrade untaped-acme once a release stops "
            "using it",
            "plugin": "acme",
            "message": "`untaped_shelf.api.human_size`, which untaped-acme 1.0 imports at "
            "untaped_acme/report.py:1, is deprecated and will be removed in untaped-shelf 5.0; "
            "use `size_text`",
        }
    ]
    forget_notices()
    assert notices() == []  # the next start reads the kept declaration


# --- what is read, and when -------------------------------------------------------------------


def test_each_plugin_is_read_once_per_version_of_its_code(site: Site, reads: list[str]) -> None:
    acme = site.acme("from untaped_shelf.api import size_text\n", requires=("untaped-shelf>=1.0",))
    shelf = site.shelf()
    compose([acme, shelf])
    assert sorted(reads) == ["untaped_acme.plugin", "untaped_shelf.plugin"]
    reads.clear()
    compose([acme, shelf])
    assert reads == []  # a warm start reads neither cache
    other = site.install(
        "untaped-other", {"untaped_other/__init__.py": "", "untaped_other/plugin.py": OTHER}
    )
    compose([acme, shelf, other])
    assert reads == ["untaped_other.plugin"]  # an unrelated new plugin reads only itself
    reads.clear()
    report = site.root / "untaped-acme" / "untaped_acme" / "report.py"
    report.write_text(report.read_text() + "\nfrom untaped_shelf.api import Book\n")
    stat = report.stat()
    os.utime(report, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    installed_code_module._install_version.cache_clear()  # the install's version, once a process
    compose([acme, shelf, other])
    assert reads == ["untaped_acme.plugin"]  # an edit reads that plugin again


def test_a_distribution_released_with_untaped_is_not_read(site: Site, reads: list[str]) -> None:
    pinned = site.install(
        "untaped-other",
        {
            "untaped_other/__init__.py": "",
            "untaped_other/plugin.py": OTHER,
            "untaped_other/later.py": "from untaped.sdk import gone\n",
        },
        at=_UNTAPED,
        requires=(f"untaped=={_UNTAPED}",),
    )
    result = compose([pinned])
    assert reads == []
    assert _composed(result) == ["untaped", "other"]


def test_two_plugins_of_one_distribution_are_each_read(site: Site) -> None:
    acme = site.acme("from untaped.sdk import plural\n")
    beta_plugin = dedent(OTHER).replace("Other", "Beta").replace("other", "beta")
    site.install(
        "untaped-acme",
        {
            "untaped_beta/__init__.py": "",
            "untaped_beta/plugin.py": beta_plugin + "\nfrom untaped.sdk import gone\n",
        },
    )
    beta = PluginCandidate(
        distribution="untaped-acme",
        name="beta",
        target="untaped_beta.plugin:Beta",
        distribution_version="1.0",
    )
    result = compose([acme, beta])
    assert _composed(result) == ["untaped", "acme"]
    reason, detail, _ = _quarantined(result)["beta"]
    assert reason == "uses-missing"
    assert detail.startswith("untaped-acme 1.0: untaped_beta/plugin.py:")
