"""What composition knows a plugin uses: the contract methods it fills, and names its load misses.

It reads no plugin's code: a name missing while the plugin loads shows from
the exception, and a deprecated import shows only when the code looks it up.
The plugins are real installs (``core_test_support.runtime.plugin_uses_site``).
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import version
from pathlib import Path
from textwrap import dedent

import pytest

from core_test_support.runtime.plugin_uses_site import FILLS_BOOKS, OTHER, Site, installed_site
from untaped.runtime import bootstrap
from untaped.runtime.declaration import UseDecl
from untaped.runtime.registry import CompositionResult, PluginCandidate, compose
from untaped.sdk.testing import CliInvoker

_UNTAPED = version("untaped")


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    with installed_site(tmp_path, monkeypatch) as installed:
        yield installed


def _quarantined(result: CompositionResult) -> dict[str, tuple[str, str, str | None]]:
    return {each.name: (each.reason, each.detail, each.fix) for each in result.quarantine}


def _composed(result: CompositionResult) -> list[str]:
    return [plugin.name for plugin in result.plugins]


# --- a missing name -------------------------------------------------------------------------


@pytest.mark.parametrize("floor", ["untaped-shelf>=1.0", "untaped-shelf>=3.0"])
def test_a_name_missing_at_load_disables_only_that_plugin_with_the_facts(
    site: Site, floor: str
) -> None:
    acme = site.acme("from untaped_shelf.api import gone\n", requires=(floor,))
    result = compose([acme, site.shelf()])
    assert _composed(result) == ["untaped", "shelf"]
    fix = "upgrade untaped-acme or untaped-shelf, or remove untaped-acme"
    assert _quarantined(result) == {
        "acme": (
            "uses-missing",
            "untaped-acme 1.0: untaped_acme/report.py:1 uses untaped_shelf.api.gone, which "
            f"untaped-shelf 3.0 doesn't have (untaped-acme requires {floor}); {fix}",
            fix,
        )
    }


def test_a_missing_name_of_untaped_without_a_requirement_says_so(site: Site) -> None:
    acme = site.acme("import json\nfrom untaped.sdk import gone, plural\n")
    [(reason, detail, fix)] = _quarantined(compose([acme])).values()
    assert reason == "uses-missing"
    assert fix == "upgrade untaped-acme or untaped, or remove untaped-acme"
    assert detail == (
        "untaped-acme 1.0: untaped_acme/report.py:2 uses untaped.sdk.gone, which "
        f"untaped {_UNTAPED} doesn't have (untaped-acme declares no requirement on untaped); "
        f"{fix}"
    )


def test_a_missing_name_the_plugin_handles_changes_nothing(site: Site) -> None:
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


def test_an_import_from_a_disabled_plugin_it_doesn_t_require_fails_only_when_reached(
    site: Site,
) -> None:
    acme = site.acme("def later():\n    from untaped_shelf.api import Book\n    return Book\n")
    shelf = site.shelf(requires=("untaped-missing>=1.0",))
    result = compose([acme, shelf])
    assert _composed(result) == ["untaped", "acme"]
    assert _quarantined(result)["shelf"][0] == "unmet-requirement"


def test_a_module_that_raises_is_an_import_failure(site: Site) -> None:
    acme = site.acme("from untaped_shelf.broken import nothing\n", requires=("untaped-shelf>=1.0",))
    reason, detail, _ = _quarantined(compose([acme, site.shelf()]))["acme"]
    assert reason == "import-failed"
    assert "RuntimeError: shelf is half installed" in detail


# --- a deprecated name ------------------------------------------------------------------------


def _uses(result: CompositionResult) -> dict[str, tuple[UseDecl, ...]]:
    return {plugin.name: plugin.uses for plugin in result.mount.declaration.plugins if plugin.uses}


def test_a_deprecated_import_is_no_use_of_the_declaration_and_warns_nothing(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    acme = site.acme(
        "def later():\n    from untaped_shelf.api import human_size\n    return human_size\n",
        requires=("untaped-shelf>=1.0",),
    )
    found = [acme, site.shelf()]
    assert _uses(compose(found)) == {}
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    result = CliInvoker().invoke(bootstrap.build_root_app(candidates=found).meta, ["--version"])
    assert result.exit_code == 0, result.output
    assert "untaped.deprecation" not in result.stderr


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


# --- two plugins in one distribution ---------------------------------------------------------


def test_two_plugins_of_one_distribution_are_each_judged_by_their_own_load(site: Site) -> None:
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
