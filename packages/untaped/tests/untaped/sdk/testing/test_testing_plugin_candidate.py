"""``untaped.sdk.testing.plugin_candidate`` composes a spec without installing it."""

from __future__ import annotations

from core_test_support.commands.support import make_plugin
from untaped.runtime.bootstrap import build_root_app, composition
from untaped.sdk.testing import CliInvoker, plugin_candidate


def test_plugin_candidate_composes_a_spec_without_installing_it(
    fresh_composition: None,
) -> None:
    spec = make_plugin("demo")
    root = build_root_app(candidates=[plugin_candidate(spec)])
    [registered] = [c for c in composition().plugins if c.name == "demo"]
    assert registered.plugin_ref.distribution == "untaped-demo"
    assert CliInvoker().invoke(root.meta, ["demo", "--help"]).exit_code == 0
