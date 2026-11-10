"""``untaped.sdk.testing.plugin`` gives any test suite the hermetic environment."""

from __future__ import annotations

import os
import pwd
import textwrap
from pathlib import Path

import pytest

from untaped.sdk._home import config_path

pytest_plugins = ["pytester"]


def test_home_and_config_are_isolated(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = Path(os.environ["HOME"])
    assert home != Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert home.is_relative_to(tmp_path_factory.getbasetemp())
    assert config_path() == home / ".untaped" / "config.yml"
    assert [n for n in os.environ if n.startswith("UNTAPED_")] == []


def test_the_plugin_alone_isolates_a_foreign_suite(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suite with no repo conftest, only the plugin, is hermetic."""
    monkeypatch.setenv("GH_TOKEN", "leak")
    monkeypatch.setenv("OUTER_HOME", os.environ["HOME"])
    pytester.makeconftest('pytest_plugins = ["untaped.sdk.testing.plugin"]')
    pytester.makepyfile(
        """
        import os
        from pathlib import Path

        from untaped.sdk._home import config_path

        def test_isolated():
            assert "GH_TOKEN" not in os.environ
            assert os.environ["HOME"] != os.environ["OUTER_HOME"]
            home = Path(os.environ["HOME"])
            assert config_path() == home / ".untaped" / "config.yml"
        """
    )
    pytester.runpytest_subprocess().assert_outcomes(passed=1)


def test_a_composition_a_test_leaves_never_reaches_the_next(pytester: pytest.Pytester) -> None:
    """A later ``kept_composition`` block re-registers only the composition its test began with."""
    pytester.makeconftest('pytest_plugins = ["untaped.sdk.testing.plugin"]')
    pytester.mkpydir("untaped_toy")
    (pytester.path / "untaped_toy" / "plugin.py").write_text(
        textwrap.dedent(
            """
            from pydantic import BaseModel

            from untaped.sdk import Plugin


            class ToySettings(BaseModel):
                \"\"\"Nothing to configure.\"\"\"


            class Toy(Plugin):
                \"\"\"A plugin.\"\"\"

                settings = ToySettings
            """
        )
    )
    pytester.makepyfile(
        """
        from untaped.runtime import bootstrap
        from untaped.runtime.settings import registered_profile_model
        from untaped.sdk.testing import plugin_candidate
        from untaped_toy.plugin import Toy


        def test_composes_and_leaves_it():
            assert not bootstrap.compose_root(candidates=[plugin_candidate(Toy)]).quarantine


        def test_finds_no_section_left():
            with bootstrap.kept_composition():
                pass
            assert registered_profile_model("toy") is None
        """
    )
    pytester.runpytest_subprocess("-p", "no:xdist").assert_outcomes(passed=2)
