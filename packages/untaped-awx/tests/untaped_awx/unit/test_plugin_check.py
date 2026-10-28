"""awx's plugin check: its lint providers checked beside their owner, under that composition."""

from __future__ import annotations

from untaped.runtime.bootstrap import composed_with
from untaped.runtime.registry import discover_candidates
from untaped.sdk import kind_of
from untaped.sdk.testing import check_plugin, compose_with
from untaped_awx.api import Resources


def test_awx_passes_its_plugin_check() -> None:
    failed = [row for row in check_plugin("awx") if row.status in ("fail", "error")]
    assert failed == []


def test_a_block_composed_inside_a_run_reads_its_own_kinds_and_gives_the_run_back() -> None:
    awx_and_lint = [each for each in discover_candidates() if each.name in ("awx", "lint")]
    with compose_with("lint"):
        assert kind_of(Resources) is None
        with composed_with(awx_and_lint):
            assert kind_of(Resources) == "awx.resources"
        assert kind_of(Resources) is None


def test_a_block_composed_outside_any_run_runs_under_its_own() -> None:
    awx_and_lint = [each for each in discover_candidates() if each.name in ("awx", "lint")]
    with composed_with(awx_and_lint):
        assert kind_of(Resources) == "awx.resources"
    assert kind_of(Resources) is None
