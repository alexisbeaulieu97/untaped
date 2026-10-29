"""Row-by-row validation tests: every violation quarantines its plugin.

Each rejection row becomes a ``QuarantineRecord`` naming the reason while
composition continues; first-party and third-party plugins are judged alike.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel, Field

from core_test_support.commands.support import make_plugin
from core_test_support.runtime.plugin_harness import make_candidate
from untaped.runtime.registry import PluginCandidate, compose


class TokenProfile(BaseModel):
    token: str = "t"


#: Each name a plugin may not have, with what holds it: core's own name, one of
#: its commands on its class, and config.yml's own keys (the grammar already
#: refuses ``format_version``).
TAKEN = {
    "untaped": "core",
    "plugin": "a root command",
    "skills": "a root command",
    "profiles": "a config key",
    "active": "a config key",
    "extensions": "a config key",
    "caches": "a config key",
    "home": "the variable UNTAPED_HOME",
}

#: Names 11.x reserved that nothing holds any more (core's settings sit under ``untaped``).
FREE = [
    "core",
    "sdk",
    "contracts",
    "plugins",
    "default",
    "state",
    "shell",
    "http",
    "ui",
    "credentials",
]


@pytest.mark.parametrize(("name", "by"), TAKEN.items(), ids=list(TAKEN))
def test_a_taken_name_is_quarantined(name: str, by: str) -> None:
    result = compose([make_candidate(make_plugin(name), "ext-dist")])
    assert [plugin.name for plugin in result.plugins] == ["untaped"]
    (record,) = result.quarantine
    assert (record.distribution, record.reason) == ("ext-dist", "duplicate-name")
    assert f"{name!r} is taken by {by}" in record.detail


@pytest.mark.parametrize("name", FREE)
def test_a_name_11x_reserved_and_nothing_holds_composes(name: str) -> None:
    result = compose([make_candidate(make_plugin(name))])
    assert result.quarantine == ()
    assert [plugin.name for plugin in result.plugins] == ["untaped", name]


# ---- entry-point targets ------------------------------------------------------


def _needs_arg(value: str) -> object:
    return make_plugin("argful")


@pytest.mark.parametrize(
    ("candidate", "reason", "entry_point", "named"),
    [
        # An unimportable target has no resolved label; the detail names it.
        (
            PluginCandidate(distribution="d", name="ghost", target="missing_mod_xyz.plugin:Ghost"),
            "malformed-entry-point",
            "",
            "missing_mod_xyz.plugin:Ghost",
        ),
        (
            PluginCandidate(distribution="d", name="ghost", target="not-a-module-ref"),
            "malformed-entry-point",
            "",
            "",
        ),
        (
            PluginCandidate(distribution="d", name="mod", target="json:decoder"),
            "not-a-plugin-class",
            "json:decoder",
            "",
        ),
        # A dotted attribute resolves and is then judged on what it is.
        (
            PluginCandidate(distribution="d", name="jsoncap", target="json.decoder:JSONDecoder"),
            "not-a-plugin-class",
            "json.decoder:JSONDecoder",
            "",
        ),
        (
            PluginCandidate(distribution="d", name="thing", target=object()),
            "not-a-plugin-class",
            "thing",
            "",
        ),
        (
            PluginCandidate(distribution="d", name="argful", target=_needs_arg),
            "not-a-plugin-class",
            "argful",
            "the callable '_needs_arg'",
        ),
        (
            make_candidate(make_plugin("raiser"), "d", error=RuntimeError("boom-text")),
            "malformed-entry-point",
            "",
            "boom-text",
        ),
        (
            make_candidate(make_plugin("wrong"), "d", result={"not": "a-class"}),
            "not-a-plugin-class",
            None,
            "dict",
        ),
    ],
    ids=[
        "unresolvable",
        "no-colon",
        "module-attr",
        "dotted-attr",
        "object",
        "callable",
        "raises",
        "not-a-class",
    ],
)
def test_bad_entry_point_target_quarantines(
    candidate: PluginCandidate, reason: str, entry_point: str | None, named: str
) -> None:
    result = compose([candidate])
    (record,) = result.quarantine
    assert record.reason == reason
    assert record.distribution == "d"
    if entry_point is not None:
        assert record.entry_point == entry_point
    assert named in record.detail


class BadKeysProfile(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"old": "nowhere"}
    value: int = 1


def test_broken_key_declarations_quarantine_only_that_plugin() -> None:
    bad = make_plugin("bad", settings=BadKeysProfile)
    good = make_plugin("good", settings=TokenProfile)

    result = compose([make_candidate(bad), make_candidate(good)])

    assert [c.name for c in result.plugins] == ["untaped", "good"]
    (record,) = result.quarantine
    assert "renamed key 'old' points at 'nowhere', which is not a setting" in record.detail


def _unset_variable() -> str:
    raise KeyError("NOPE")


class LazyProfile(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"old_dir": "cache_dir"}
    cache_dir: str = Field(default_factory=_unset_variable)


def test_a_raising_default_factory_is_not_called_while_composing() -> None:
    lazy = make_plugin("lazy", settings=LazyProfile)
    good = make_plugin("good", settings=TokenProfile)

    result = compose([make_candidate(lazy), make_candidate(good)])

    assert ([c.name for c in result.plugins], result.quarantine) == (
        ["untaped", "good", "lazy"],
        (),
    )
