"""Commands run through the composed CLI, in a fresh interpreter, as a user's start runs them.

In-process tests compose with ``untaped.sdk.testing.install()``, which
imports every required plugin's class up front. A real start imports only
the classes its command needs, so a dependent plugin reading a dependency's
block through the dependency's ``api`` is checked here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _untaped(home: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    return subprocess.run(
        [sys.executable, "-m", "untaped", *argv],
        capture_output=True,
        text=True,
        check=False,
        env={**env, "UNTAPED_HOME": str(home)},
    )


def test_a_dependent_plugin_reads_its_dependencys_credential(tmp_path: Path) -> None:
    # ansible reads github's token through untaped_github.api; no token is
    # set, so the refresh stops before the network.
    assert _untaped(tmp_path, "ansible", "source", "set", "s", "--repo", "o/n").returncode == 0
    refresh = _untaped(tmp_path, "ansible", "source", "refresh", "s")
    assert "Traceback" not in refresh.stderr
    assert refresh.stderr.startswith("error: no github token"), refresh.stderr
    assert refresh.returncode == 4
