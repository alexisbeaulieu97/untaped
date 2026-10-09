"""``untaped.testing.provider_candidate`` composes a spec without installing it."""

from __future__ import annotations

from test_management.support import make_spec
from untaped.bootstrap import build_root_app, composition
from untaped.testing import CliInvoker, provider_candidate


def test_provider_candidate_composes_a_spec_without_installing_it(
    fresh_composition: None,
) -> None:
    spec = make_spec("demo")
    root = build_root_app(candidates=[provider_candidate(spec)])
    [registered] = [c for c in composition().plugins if c.spec.name == "demo"]
    assert registered.provider_ref.distribution == "test-provider"
    assert CliInvoker().invoke(root.meta, ["demo", "--help"]).exit_code == 0
