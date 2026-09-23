"""Ctrl-C during ``workspace foreach --parallel`` stops running commands promptly."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from untaped.capabilities.workspace.cli import app
from untaped.testing import CliInvoker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.name == "nt", reason="POSIX process groups and SIGINT"),
]


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_sigint_terminates_running_parallel_commands(tmp_path: Path) -> None:
    cli = CliInvoker()
    target = tmp_path / "ws"
    assert cli.invoke(app, ["init", "prod", "--path", str(target)]).exit_code == 0
    for name in ("a", "b", "c"):
        added = cli.invoke(
            app, ["add", f"https://x/{name}.git", "--repo-name", name, "--workspace", "prod"]
        )
        assert added.exit_code == 0, added.output
        (target / name).mkdir()
    pids = tmp_path / "pids"
    command = f"echo $$ >> {pids}; sleep 20"

    proc = subprocess.Popen(
        [
            *(sys.executable, "-m", "untaped", "workspace", "foreach", command),
            *("--workspace", "prod", "-j", "2"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if pids.exists() and len(pids.read_text().split()) >= 2:
                break
            time.sleep(0.05)
        else:
            pytest.fail("parallel commands never started")
        started = time.monotonic()
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=10)
        elapsed = time.monotonic() - started
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    assert proc.returncode != 0
    assert elapsed < 5
    groups = [int(pid) for pid in pids.read_text().split()]
    assert len(groups) == 2, "queued repo must not start after Ctrl-C"
    deadline = time.monotonic() + 2
    while any(_group_alive(pgid) for pgid in groups) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any(_group_alive(pgid) for pgid in groups)
