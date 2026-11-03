"""What core renders of a server API from the declaration: the docs section and the surface lines.

The plugin's page gains "Servers" (detection, the max-age setting, the
override, ``detect``'s docstring and a row per adapter); the surface gains
the block's line and one line per adapter, a deprecated adapter's carrying
its major and replacement so the release check holds it to them.
"""

from __future__ import annotations

import pytest

from core_test_support.server_api import Toy
from core_test_support.server_api_legacy import Legacy
from untaped.core.surface import DeprecatedMark, Line, due, plugin_lines
from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.registry import PluginCandidate
from untaped.sdk.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")

TOY = PluginCandidate(distribution="untaped-toy", name="toy", target=Toy)
LEGACY = PluginCandidate(distribution="untaped-legacy", name="legacy", target=Legacy)


def _page(candidate: PluginCandidate) -> str:
    root = build_root_app(candidates=[candidate])
    ran = CliInvoker().invoke(root.meta, ["docs", candidate.name])
    assert ran.exit_code == 0, ran.stderr
    return ran.stdout


def test_the_plugin_page_says_how_its_server_version_is_found() -> None:
    page = _page(TOY)
    servers = page[page.index("## Servers") :]
    assert servers.splitlines()[:9] == [
        "## Servers",
        "",
        "untaped-toy detects which version its server runs on first use and checks again "
        "after 24 h (`toy.caches.server_api.max_age_seconds`); `toy.server_version`, set "
        "beside `toy.server.url`, says it instead.",
        "",
        "Asks `GET /about`.",
        "",
        "| Server | How | Recorded against | |",
        "|---|---|---|---|",
        "| Toy 3 and later | API v2. | Toy 3.0 |  |",
    ]
    assert "| Toy 1 to 2 | API v1. | Toy 1.4 |  |" in servers


def test_a_deprecated_adapter_row_says_when_it_goes() -> None:
    page = _page(LEGACY)
    row = (
        "| Toy 1 to 2 | API v1, deprecated. | Toy 1.4 "
        "| deprecated, removed in untaped-legacy 13.0 |"
    )
    assert row in page


def test_the_surface_carries_the_block_and_each_adapter() -> None:
    lines = [line for line in plugin_lines(TOY) if line.area in {"server-api", "adapter"}]
    assert lines == [
        Line("adapter toy.server-api V1", "experimental"),
        Line("adapter toy.server-api V2", "experimental"),
        Line("server-api toy.server-api override=toy.server_version", "experimental"),
    ]


def test_a_deprecated_adapter_line_names_its_major_and_replacement() -> None:
    lines = {line.text: line.mark for line in plugin_lines(LEGACY)}
    assert lines["adapter legacy.server-api OldV1"] == DeprecatedMark(13, "V2")


def test_the_release_check_holds_a_deprecated_adapter_to_its_major() -> None:
    lines = plugin_lines(LEGACY)
    assert [line.text for line in due(lines, 13) if line.area == "adapter"] == [
        "adapter legacy.server-api OldV1"
    ]
    assert not [line for line in due(lines, 12) if line.area == "adapter"]
