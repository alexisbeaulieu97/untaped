"""``untaped.testing.plugin`` gives any test suite the hermetic environment."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def test_home_and_config_are_isolated(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = Path(os.environ["HOME"])
    assert home != Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert home.is_relative_to(tmp_path_factory.getbasetemp())
    assert os.environ["UNTAPED_CONFIG"] == str(home / ".untaped" / "config.yml")
    assert [n for n in os.environ if n.startswith("UNTAPED_")] == ["UNTAPED_CONFIG"]


def test_the_plugin_alone_isolates_a_foreign_suite(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suite with no repo conftest, only the plugin, is hermetic."""
    monkeypatch.setenv("GH_TOKEN", "leak")
    monkeypatch.setenv("OUTER_HOME", os.environ["HOME"])
    pytester.makeconftest('pytest_plugins = ["untaped.testing.plugin"]')
    pytester.makepyfile(
        """
        import os
        from pathlib import Path

        def test_isolated():
            assert "GH_TOKEN" not in os.environ
            assert os.environ["HOME"] != os.environ["OUTER_HOME"]
            home = Path(os.environ["HOME"])
            assert os.environ["UNTAPED_CONFIG"] == str(home / ".untaped" / "config.yml")
        """
    )
    pytester.runpytest_subprocess().assert_outcomes(passed=1)
