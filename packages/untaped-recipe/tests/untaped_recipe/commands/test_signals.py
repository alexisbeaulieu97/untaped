"""Signals to untaped during ``recipe apply`` reach the pack's hook worker."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from recipe_pack_support import write_hook_project

_HOOK = """\
import os, pathlib, signal, subprocess, sys, time


def validate(*, inputs, target, args, helpers):
    marks = pathlib.Path(args["marks"]) / pathlib.Path(target).name
    marks.mkdir()

    def note(signum, frame):  # records each soft signal and runs on
        with (marks / "signals").open("a") as signals:
            signals.write(signal.Signals(signum).name + "\\n")

    signal.signal(signal.SIGINT, note)
    signal.signal(signal.SIGTERM, note)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (marks / "pids.tmp").write_text(f"{os.getpid()} {child.pid}")
    os.replace(marks / "pids.tmp", marks / "pids")
    while True:
        time.sleep(0.05)
"""


def _gone(pid: int) -> bool:
    """Gone, or a zombie waiting for a reaper (it no longer runs)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return False


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups (#1013)")
@pytest.mark.parametrize(
    ("sig", "parallel"),
    [(signal.SIGINT, 1), (signal.SIGTERM, 1), (signal.SIGINT, 4)],
    ids=["sigint", "sigterm", "sigint-parallel"],
)
def test_a_signal_to_untaped_reaches_the_hook_and_what_it_started(
    tmp_path: Path, sig: signal.Signals, parallel: int
) -> None:
    if sig == signal.SIGINT and signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
        pytest.skip("SIGINT is ignored here (a background job), so untaped inherits that")
    marks = tmp_path / "marks"
    marks.mkdir()
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        f"version: 1\nsteps:\n  - type: validate\n    hook: hang\n    args:\n      marks: {marks}\n"
    )
    write_hook_project(recipe_dir, public_name="hang", module_name="hang", code=_HOOK)
    targets = [tmp_path / f"target{i}" for i in range(parallel)]
    for target in targets:
        target.mkdir()
    untaped = subprocess.Popen(
        [sys.executable, "-m", "untaped", "recipe", "apply", str(recipe_dir)]
        + [str(target) for target in targets]
        + ["--yes", "--parallel", str(parallel)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pid_files = [marks / target.name / "pids" for target in targets]
    pids: list[int] = []
    try:
        deadline = time.monotonic() + 120  # uv builds the pack's environment first
        while not all(pid_file.exists() for pid_file in pid_files):
            assert untaped.poll() is None, untaped.communicate()[1]
            assert time.monotonic() < deadline, "the hooks did not start"
            time.sleep(0.05)
        pids = [int(pid) for pid_file in pid_files for pid in pid_file.read_text().split()]
        untaped.send_signal(sig)
        signalled = time.monotonic()
        _, stderr = untaped.communicate(timeout=15)
        # One grace for every worker together, not one each or the hook timeout (60 s).
        assert time.monotonic() - signalled < 6, stderr
        deadline = time.monotonic() + 5
        while not all(_gone(pid) for pid in pids):
            assert time.monotonic() < deadline, "the hook outlived untaped"
            time.sleep(0.05)
    finally:
        untaped.kill()
        for pid in pids:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
    # uv passes the signal on to the worker as well, so it may come twice; it
    # is the only soft signal (no SIGTERM after a Ctrl-C), and SIGKILL follows.
    for target in targets:
        assert set((marks / target.name / "signals").read_text().split()) == {sig.name}
    exit_status = 130 if sig == signal.SIGINT else -sig  # Ctrl-C's status; else dies of it
    assert (untaped.returncode, "Traceback" in stderr) == (exit_status, False), stderr
