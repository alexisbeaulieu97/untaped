"""``untaped.testing.plugin_candidate`` composes a spec without installing it."""

from __future__ import annotations

from test_management.support import make_spec
from untaped.bootstrap import build_root_app, composition
from untaped.testing import CliInvoker, plugin_candidate


def test_plugin_candidate_composes_a_spec_without_installing_it(
    fresh_composition: None,
) -> None:
    spec = make_spec("demo")
    root = build_root_app(candidates=[plugin_candidate(spec)])
    [registered] = [c for c in composition().plugins if c.spec.name == "demo"]
    assert registered.plugin_ref.distribution == "untaped-demo"
    assert CliInvoker().invoke(root.meta, ["demo", "--help"]).exit_code == 0
