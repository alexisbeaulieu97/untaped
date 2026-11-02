"""Process boundary: plugins start child groups through ``untaped.sdk.process``, set no handlers."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from lint_test_support import Site
from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions import file_lines
from untaped_lint.providers.conventions.process_boundary import (
    GROUP_MESSAGE,
    GROUP_RULE,
    SIGNAL_MESSAGE,
    SIGNAL_RULE,
    process_boundary_violations,
)

pytestmark = pytest.mark.usefixtures("fresh_composition")

GROUP = f"::{GROUP_RULE}::{GROUP_MESSAGE}"
SIGNAL = f"::{SIGNAL_RULE}::{SIGNAL_MESSAGE}"


def _violations(tmp_path: Path, source: str) -> list[str]:
    source_dir = tmp_path / "acme"
    source_dir.mkdir()
    (source_dir / "tool.py").write_text(dedent(source), encoding="utf-8")
    return process_boundary_violations(source_dir, list(source_files(source_dir)))


def test_the_rules_are_named() -> None:
    assert (GROUP_RULE, SIGNAL_RULE) == ("process-group", "signal-handler")


def test_the_messages_say_what_they_protect() -> None:
    assert "left running when untaped is interrupted" in GROUP_MESSAGE
    assert "stops every plugin's child processes" in SIGNAL_MESSAGE


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.Popen(['x'], start_new_session=True)\n",
        "import subprocess\nsubprocess.run(['x'], process_group=0)\n",
        "import os, subprocess\nsubprocess.Popen(['x'], preexec_fn=os.setsid)\n",
        "import os\nos.posix_spawn('x', ['x'], {}, setsid=True)\n",
        "import os\nos.setsid()\n",
        "import os\nos.setpgid(0, 0)\n",
        "import os\nos.killpg(4, 15)\n",
        "from os import killpg\nkillpg(4, 15)\n",
        "import os as system\nsystem.killpg(4, 15)\n",
        "from os import killpg as stop\nstop(4, 15)\n",
        "import os\nos.kill(-4, 15)\n",
        "import os\nos.setpgrp()\n",
        "import os, subprocess\nsubprocess.Popen(['x'], preexec_fn=os.setpgrp)\n",
        "import os\nos.posix_spawnp('x', ['x'], {}, setpgroup=0)\n",
        "import pty\npty.fork()\n",
        "import os\nos.forkpty()\n",
        "import pty\npty.spawn(['x'])\n",
        "import os\nos.login_tty(3)\n",
    ],
)
def test_a_group_by_hand_is_a_violation(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source) == [f"acme/tool.py:2{GROUP}"]


@pytest.mark.parametrize(
    "source",
    [
        "import signal\nsignal.signal(signal.SIGTERM, print)\n",
        "from signal import signal, SIGINT\nsignal(SIGINT, print)\n",
        "import asyncio, signal\nasyncio.get_running_loop().add_signal_handler(15, print)\n",
        "import signal\nsignal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})\n",
    ],
)
def test_a_signal_handler_is_a_violation(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source) == [f"acme/tool.py:2{SIGNAL}"]


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(['x'], start_new_session=False)\n",
        "import os\nos.kill(4, 15)\n",
        "import signal\nsignal.getsignal(signal.SIGTERM)\n",
        "x = 'os.killpg(4, 15)'  # mentioned, not called\n",
        '"""Its background processes are stopped, unless in their own session (`setsid`)."""\n',
        "from untaped.sdk.process import run_process\nrun_process(['x'], timeout=5)\n",
        "def connect(**kwargs): ...\nconnect(setsid=True, setpgroup=1)\n",
    ],
)
def test_clean_files_pass(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source) == []


def test_a_waiver_allows_one_call(tmp_path: Path) -> None:
    source = (
        "import os\nos.killpg(4, 15)  # untaped: allow process-group\n"
        "os.killpg(4, 9)\nimport signal\nsignal.signal(2, print)  # untaped: allow signal-handler\n"
    )
    assert _violations(tmp_path, source) == [f"acme/tool.py:3{GROUP}"]


def test_lint_reports_both_for_a_plugin(site: Site) -> None:
    root = site(
        {
            "demo/__init__.py": "",
            "demo/tool.py": "import os, signal\nos.setsid()\nsignal.signal(15, print)\n",
        }
    )
    found = file_lines("demo", root / "demo", plugin="demo", plugins=[])
    assert f"demo/tool.py:2{GROUP}" in found
    assert f"demo/tool.py:3{SIGNAL}" in found
