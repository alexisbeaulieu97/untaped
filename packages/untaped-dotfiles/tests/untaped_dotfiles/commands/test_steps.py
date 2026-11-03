"""Steps: a check, then what fixes it, run only as approved (design 4.5, 7.3, 8).

Each step here runs a small recorder (``probe.py``) that logs every call, so
a test sees exactly which checks and runs happened, and when.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from dotfiles_test_support import (
    MakeRemote,
    as_json,
    installed,
    json_rows,
    publish,
    run,
    states,
    subscribe,
)
from untaped.sdk.testing import ScriptedTerminal

pytestmark = pytest.mark.usefixtures("dotfiles_env")

_PROBE = """\
import json, os, pathlib, sys
log, mark, what = sys.argv[1:4]
with open(log, "a") as f:
    f.write(" ".join([what, *sys.argv[4:]]) + "\\n")
if what == "check":
    sys.exit(0 if pathlib.Path(mark).exists() else 1)
if what == "env":
    pathlib.Path(mark).write_text(json.dumps(dict(os.environ)))
    sys.exit(0)
data = sys.stdin.read()
print("got", data, *sys.argv[4:])
print("on stderr", data, file=sys.stderr)
if what == "run":
    pathlib.Path(mark).write_text(data)
sys.exit(4 if what == "broken" else 0)
"""

SECRET = "sentinel-7c1e-secret"


@dataclass(frozen=True)
class Probe:
    """Where the recorder logs its calls, and the file its check looks for."""

    script: Path
    log: Path
    mark: Path

    def argv(self, what: str, *extra: str) -> str:
        """A YAML flow list running the recorder as ``what``."""
        argv = [sys.executable, str(self.script), str(self.log), str(self.mark), what, *extra]
        return json.dumps(argv)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def probe(tmp_path: Path) -> Iterator[Probe]:
    folder = tmp_path / "probe"
    folder.mkdir()
    script = folder / "probe.py"
    script.write_text(_PROBE)
    yield Probe(script, folder / "log", folder / "mark")


_FILES = "    files:\n      - {path: tool.txt, target: ~/tool.txt, mode: copy}\n"


def _tool(
    probe: Probe,
    *,
    policy: str = "manual",
    run_as: str = "run",
    step: str = "",
    inputs: str = "",
    files: bool = False,
) -> str:
    """A package ``tool`` with one item ``tool`` and one step ``install``."""
    return f"""\
version: 1
description: one step
policy: {policy}
{"inputs:" + chr(10) + inputs if inputs else ""}items:
  tool:
{_FILES if files else ""}\
    steps:
      - name: install
        check: {{command: {probe.argv("check")}}}
        run: {probe.argv(run_as)}
{step}"""


def _source(make_remote: MakeRemote, package: str):  # type: ignore[no-untyped-def]
    return make_remote(packages={"tool": package}, files={"tool.txt": "tool\n"})


def _step_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [row for row in rows if row["kind"] == "step"]


def _apply(*args: str) -> tuple[int, list[dict[str, object]], str]:
    result = run(["apply", *args, "--format", "json"])
    return result.exit_code, _step_rows(json_rows(result)), result.stderr


def _state_files(home: Path) -> str:
    root = home / ".untaped"
    files = [root / "config.yml", *(root / "plugins" / "dotfiles").rglob("*")]
    return "".join(path.read_text(errors="replace") for path in files if path.is_file())


# -- Steps (8) ---------------------------------------------------------------------


def test_a_check_that_passes_runs_nothing(make_remote: MakeRemote, probe: Probe) -> None:
    subscribe(_source(make_remote, _tool(probe)).url, "--add", "tool")
    probe.mark.write_text("there")
    code, rows, _ = _apply("--yes")
    assert code == 0
    assert [(r["entry"], r["action"]) for r in rows] == [("install", "unchanged")]
    assert rows[0]["detail"] == "the check passes; nothing ran"
    assert probe.calls() == ["check"]
    assert states()["install"] == "applied"


def test_a_failing_check_runs_the_step_then_checks_again(
    make_remote: MakeRemote, probe: Probe
) -> None:
    subscribe(_source(make_remote, _tool(probe)).url, "--add", "tool")
    preview = run(["apply", "--dry-run"])
    assert "runs: " in preview.stderr and "fails" in preview.stderr
    assert probe.calls() == []  # a dry run of a step never approved runs nothing
    code, rows, stderr = _apply("--yes")
    assert code == 0, stderr
    assert [(r["action"], r["state"]) for r in rows] == [("created", "pending")]
    assert rows[0]["detail"] == "ran; the check passes now"
    assert probe.calls() == ["check", "run", "check"]
    assert "got" in stderr  # what the run printed is relayed
    assert states()["install"] == "applied"
    assert _apply("--yes")[0] == 0
    assert probe.calls()[-1] == "check"  # applied: only the check, nothing ran


def test_a_run_that_leaves_the_check_failing_fails_its_row(
    make_remote: MakeRemote, probe: Probe
) -> None:
    subscribe(_source(make_remote, _tool(probe, run_as="noop")).url, "--add", "tool")
    code, rows, _ = _apply("--yes")
    assert code == 1
    assert rows[0]["action"] == "failed"
    assert "ran, but the check still fails" in str(rows[0]["detail"])
    assert states()["install"] == "pending"  # not stored: the next apply tries again


def test_a_run_that_exits_non_zero_fails_its_row(make_remote: MakeRemote, probe: Probe) -> None:
    subscribe(_source(make_remote, _tool(probe, run_as="broken")).url, "--add", "tool")
    code, rows, _ = _apply("--yes")
    assert code == 1
    assert rows[0]["action"] == "failed" and "exits 4" in str(rows[0]["detail"])
    assert probe.calls() == ["check", "broken"]


def test_a_report_only_step_exits_3_with_its_fix(make_remote: MakeRemote, probe: Probe) -> None:
    package = f"""\
version: 1
policy: manual
items:
  tool:
    steps:
      - name: install
        check: {{command: {probe.argv("check")}}}
        fix: install it from the vendor's site
"""
    subscribe(_source(make_remote, package).url, "--add", "tool")
    preview = run(["apply", "--dry-run"])
    assert "report only, if" in preview.stderr
    code, rows, _ = _apply("--yes")
    assert code == 3
    assert rows[0]["action"] == "skipped"
    assert rows[0]["detail"] == "needs you: install it from the vendor's site"
    assert states()["install"] == "unmet"
    probe.mark.write_text("done by hand")
    assert states()["install"] == "applied"


def test_a_step_whose_check_fails_later_is_drifted(make_remote: MakeRemote, probe: Probe) -> None:
    subscribe(_source(make_remote, _tool(probe)).url, "--add", "tool")
    assert _apply("--yes")[0] == 0
    probe.mark.unlink()
    rows = {r["entry"]: r for r in as_json(["status"])}
    assert rows["install"]["state"] == "drifted"
    assert "its check fails now" in str(rows["install"]["detail"])
    calls = len(probe.calls())
    assert _apply("--yes")[0] == 3  # manual: a drifted step waits for a person
    assert "run" not in probe.calls()[calls:]
    code, rows_, _ = _apply("tool", "--yes")
    assert code == 0 and rows_[0]["action"] == "updated"
    assert states()["install"] == "applied"


def test_a_changed_step_under_manual_is_behind_until_asked(
    make_remote: MakeRemote, probe: Probe
) -> None:
    remote = _source(make_remote, _tool(probe))
    subscribe(remote.url, "--add", "tool")
    assert _apply("--yes")[0] == 0
    publish(remote, {".untaped/dotfiles/tool.yml": _tool(probe).replace('"run"]', '"run", "v2"]')})
    probe.mark.unlink()
    calls = len(probe.calls())
    code, rows, _ = _apply("--yes")
    assert code == 3 and rows[0]["state"] == "behind"
    assert probe.calls()[calls:] == []  # a changed step isn't approved: nothing of it runs
    assert states()["install"] == "behind"
    assert _apply("tool", "--yes")[0] == 0
    assert probe.calls()[calls:] == ["check", "run v2", "check"]


def test_a_step_kind_no_plugin_provides_is_unmet(make_remote: MakeRemote) -> None:
    package = """\
version: 1
items:
  tool:
    steps:
      - name: fonts
        kind: font
        with: {name: fira}
"""
    subscribe(_source(make_remote, package).url, "--add", "tool")
    rows = {r["entry"]: r for r in as_json(["status"])}
    assert rows["fonts"]["state"] == "unmet"
    assert "step kind 'font'" in str(rows["fonts"]["detail"])
    assert _apply("--yes")[0] == 3


@pytest.mark.skipif(sys.platform == "win32", reason="a script with a shebang")
def test_a_script_step_runs_the_file_from_the_source(make_remote: MakeRemote, probe: Probe) -> None:
    package = f"""\
version: 1
items:
  tool:
    steps:
      - name: setup
        check: {{path: {json.dumps(str(probe.mark))}}}
        script: bin/setup.sh
        args: [one, "{{{{ who }}}}"]
inputs:
  who: {{description: Who}}
"""
    remote = make_remote(packages={"tool": package}, files={})
    script = remote.work / "bin/setup.sh"
    script.parent.mkdir()
    script.write_text(f'#!/bin/sh\necho "$@" > "{probe.mark}"\n')
    script.chmod(0o755)
    publish(remote, {})
    subscribe(remote.url, "--add", "tool")
    code, _, stderr = _apply("--yes", "--var", "who=me")
    assert code == 0, stderr
    assert probe.mark.read_text() == "one me\n"
    assert states()["setup"] == "applied"
    publish(remote, {"bin/setup.sh": f'#!/bin/sh\necho "v2 $@" > "{probe.mark}"\n'})
    assert run(["apply", "--dry-run"]).exit_code == 3  # fetches
    assert states()["setup"] == "behind"  # the script's content is part of what was approved


# -- Inputs and secrets --------------------------------------------------------------


def _with_step_input(probe: Probe) -> str:
    step = "        inputs:\n          tag: {description: A tag for this run}\n"
    package = _tool(probe, inputs="  who: {description: Who}\n")
    return package.replace(probe.argv("run"), probe.argv("run", "{{ tag }}", "{{ who }}")) + step


def test_a_step_input_is_asked_each_run_and_never_kept(
    make_remote: MakeRemote, probe: Probe, terminal: ScriptedTerminal, user_home: Path
) -> None:
    subscribe(_source(make_remote, _with_step_input(probe)).url, "--add", "tool")
    terminal.types("me", "tag-first").answers(True)
    try:
        done = run(["apply"])
    finally:
        terminal.absent()
    assert done.exit_code == 0, done.output
    assert probe.calls()[-2] == "run tag-first me"
    assert "tag-first" not in _state_files(user_home)
    assert "me" in (user_home / ".untaped/config.yml").read_text()  # a package input is kept
    probe.mark.unlink()
    terminal.types("tag-second").answers(True)
    try:
        again = run(["apply", "tool"])
    finally:
        terminal.absent()
    assert again.exit_code == 0, again.output
    assert probe.calls()[-2] == "run tag-second me"


def test_var_answers_a_step_input_for_the_run(
    make_remote: MakeRemote, probe: Probe, user_home: Path
) -> None:
    subscribe(_source(make_remote, _with_step_input(probe)).url, "--add", "tool")
    code, _, stderr = _apply("--yes", "--var", "who=me", "--var", "tag=tag-flag")
    assert code == 0, stderr
    assert probe.calls()[-2] == "run tag-flag me"
    assert "tag-flag" not in _state_files(user_home)


def test_with_no_terminal_a_step_input_skips_the_step(
    make_remote: MakeRemote, probe: Probe
) -> None:
    subscribe(_source(make_remote, _with_step_input(probe)).url, "--add", "tool")
    code, rows, _ = _apply("--yes", "--var", "who=me")
    assert code == 3
    assert rows[0]["action"] == "skipped"
    assert "--var tag=VALUE" in str(rows[0]["detail"])
    assert probe.calls() == ["check"]  # its inputs are asked only once the check fails
    assert states()["install"] == "pending"
    probe.mark.write_text("there")
    code, rows, _ = _apply("--yes", "--var", "who=me")
    assert code == 0 and rows[0]["action"] == "unchanged"  # a passing check asks nothing


def test_a_secret_reaches_only_the_programs_stdin(
    make_remote: MakeRemote, probe: Probe, terminal: ScriptedTerminal, user_home: Path
) -> None:
    secret = "  token: {description: A token, secret: true}\n"
    package = _tool(probe, inputs=secret) + "        stdin: token\n"
    subscribe(_source(make_remote, package).url, "--add", "tool")
    refused = run(["apply", "--yes", "--var", f"token={SECRET}"])
    assert refused.exit_code == 2
    assert "is asked at a terminal" in refused.stderr
    terminal.types(SECRET).answers(True)
    try:
        done = run(["apply", "--format", "json"])
    finally:
        terminal.absent()
    assert done.exit_code == 0, done.output
    assert probe.mark.read_text() == SECRET  # it reached the program
    assert "got ***" in done.stderr and "on stderr ***" in done.stderr
    for where in (done.stdout, done.stderr, _state_files(user_home)):
        assert SECRET not in where
    status = run(["status", "--format", "json"])
    assert SECRET not in status.output


# -- Approval (8) --------------------------------------------------------------------


def test_nothing_is_checked_before_the_confirmation(
    make_remote: MakeRemote, probe: Probe, terminal: ScriptedTerminal
) -> None:
    remote = _source(make_remote, _tool(probe, files=True))
    subscribe(remote.url, "--add", "tool")
    run(["show", "tool", "--format", "json"])
    assert states()["install"] == "pending"
    terminal.answers(False)
    try:
        declined = run(["apply"])
    finally:
        terminal.absent()
    assert declined.exit_code == 1
    assert probe.calls() == []  # subscribe, show, status and a declined apply ran nothing


def test_an_authors_sync_doesnt_run_a_changed_step(make_remote: MakeRemote, probe: Probe) -> None:
    remote = _source(make_remote, _tool(probe, policy="sync", files=True))
    subscribed = subscribe(remote.url, "--add", "tool")
    assert "suggests sync and has steps" in subscribed.stderr
    assert "add dotfiles/tool --policy sync" in subscribed.stderr
    assert _apply("--yes")[0] == 0
    changed = _tool(probe, policy="sync", files=True).replace('"run"]', '"run", "v2"]')
    publish(remote, {".untaped/dotfiles/tool.yml": changed, "tool.txt": "tool v2\n"})
    probe.mark.unlink()
    calls = len(probe.calls())
    code, rows, _ = _apply("--yes")
    assert code == 3 and rows[0]["state"] == "behind"
    assert probe.calls()[calls:] == []
    rows_ = {r["entry"]: r for r in as_json(["status"])}
    assert (rows_["install"]["policy"], rows_["install"]["chosen"]) == ("manual", False)
    assert rows_["~/tool.txt"]["policy"] == "sync"
    [detail] = as_json(["show", "tool"])
    [step] = detail["items"][0]["steps"]  # type: ignore[index]
    assert (step["policy"], step["chosen"]) == ("manual", False)
    as_json(["add", "tool", "--policy", "sync"])
    assert _apply("--yes")[0] == 0
    assert probe.calls()[calls:] == ["check", "run v2", "check"]


# -- Environment (8) -----------------------------------------------------------------


def test_a_step_runs_with_the_environment_untaped_received(
    make_remote: MakeRemote, probe: Probe, user_home: Path
) -> None:
    package = _tool(probe).replace(probe.argv("check"), probe.argv("env"))
    subscribe(_source(make_remote, package).url, "--add", "tool")
    _apply("--yes")
    assert json.loads(probe.mark.read_text()) == dict(os.environ)


# -- forgetting ----------------------------------------------------------------------


def test_remove_forgets_the_steps_and_undoes_nothing(make_remote: MakeRemote, probe: Probe) -> None:
    subscribe(_source(make_remote, _tool(probe)).url, "--add", "tool")
    assert _apply("--yes")[0] == 0
    rows = _step_rows(as_json(["remove", "tool", "--yes"]))
    assert [(r["entry"], r["action"]) for r in rows] == [("install", "deleted")]
    assert rows[0]["detail"] == "forgotten; nothing it did is undone"
    assert probe.mark.exists()
    as_json(["add", "tool"])
    assert states()["install"] == "pending"


def test_unsubscribe_forgets_the_steps(make_remote: MakeRemote, probe: Probe) -> None:
    subscribe(_source(make_remote, _tool(probe)).url, "--add", "tool")
    assert _apply("--yes")[0] == 0
    rows = _step_rows(as_json(["unsubscribe", "dotfiles", "--yes"]))
    assert [(r["entry"], r["action"]) for r in rows] == [("install", "deleted")]
    assert not installed().cli(["dotfiles", "status", "--format", "json"]).stdout.count("install")


def test_an_applied_step_of_an_item_turned_off_is_off(
    make_remote: MakeRemote, probe: Probe
) -> None:
    subscribe(_source(make_remote, _tool(probe)).url, "--add", "tool")
    assert _apply("--yes")[0] == 0
    ran = installed().cli(
        ["config", "set", "dotfiles.sources.dotfiles.packages.tool.items.tool.enabled", "false"]
    )
    assert int(ran.exit) == 0, ran.stderr
    calls = len(probe.calls())
    rows = {r["entry"]: r for r in as_json(["status"])}
    assert rows["install"]["state"] == "off"
    assert probe.calls()[calls:] == []  # a step that is off runs nothing, its check included
    assert "remove dotfiles/tool/tool" in str(rows["install"]["detail"])
    rows_ = _step_rows(as_json(["remove", "tool/tool", "--yes"]))
    assert [r["action"] for r in rows_] == ["deleted"]


def test_a_step_the_author_drops_is_forgotten_under_sync(
    make_remote: MakeRemote, probe: Probe
) -> None:
    remote = _source(make_remote, _tool(probe, files=True))
    subscribe(remote.url, "--add", "tool")
    as_json(["add", "tool", "--policy", "sync"])
    assert _apply("--yes")[0] == 0
    dropped = _tool(probe, files=True).split("    steps:")[0]
    publish(remote, {".untaped/dotfiles/tool.yml": dropped})
    assert run(["apply", "--dry-run"]).exit_code == 0  # fetches
    assert states()["install"] == "orphan"
    code, rows, _ = _apply("--yes")
    assert code == 0
    assert [(r["entry"], r["action"]) for r in rows] == [("install", "deleted")]
    assert "install" not in states()


def test_apply_pkg_forgets_a_step_the_author_drops_under_manual(
    make_remote: MakeRemote, probe: Probe
) -> None:
    remote = _source(make_remote, _tool(probe, files=True))
    subscribe(remote.url, "--add", "tool")
    assert _apply("--yes")[0] == 0
    publish(remote, {".untaped/dotfiles/tool.yml": _tool(probe, files=True).split("    steps:")[0]})
    code, rows, _ = _apply("--yes")
    assert code == 3 and rows[0]["state"] == "orphan"  # manual: reported
    code, rows, _ = _apply("tool", "--yes")
    assert code == 0 and [r["action"] for r in rows] == ["deleted"]
    assert "install" not in states()


def test_a_step_that_failed_stays_pending_not_new(make_remote: MakeRemote, probe: Probe) -> None:
    subscribe(_source(make_remote, _tool(probe, run_as="noop", files=True)).url, "--add", "tool")
    code, rows, _ = _apply("--yes")
    assert code == 1 and rows[0]["action"] == "failed"
    assert states()["~/tool.txt"] == "applied"
    assert states()["install"] == "pending"  # asked for with its item: not added later


def test_an_argument_starting_with_tilde_names_the_home_folder(
    make_remote: MakeRemote, probe: Probe, user_home: Path
) -> None:
    package = _tool(probe).replace(probe.argv("run"), probe.argv("run", "~/out", "a~/b"))
    subscribe(_source(make_remote, package).url, "--add", "tool")
    assert _apply("--yes")[0] == 0
    assert f"run {user_home / 'out'} a~/b" in probe.calls()


def test_a_plain_input_read_on_stdin_waits_for_a_value(
    make_remote: MakeRemote, probe: Probe
) -> None:
    package = _tool(probe, inputs="  token: {description: A token}\n") + "        stdin: token\n"
    subscribe(_source(make_remote, package).url, "--add", "tool")
    rows = {r["entry"]: r for r in as_json(["status"])}
    assert rows["install"]["state"] == "pending"
    assert "add dotfiles/tool --var token=VALUE" in str(rows["install"]["detail"])
    assert _apply("--yes")[0] == 3
    assert probe.calls() == []
    assert _apply("--yes", "--var", "token=abc")[0] == 0
    assert probe.mark.read_text() == "abc"
    probe.mark.unlink()
    code, rows_, _ = _apply("--yes", "--var", "token=xyz")
    assert code == 0 and rows_[0]["state"] == "pending"  # a changed input asks again
    assert probe.mark.read_text() == "xyz"


@pytest.mark.skipif(sys.platform == "win32", reason="a script with a shebang")
def test_a_stuck_clone_runs_none_of_its_steps(
    make_remote: MakeRemote, probe: Probe, tmp_path: Path
) -> None:
    package = f"""\
version: 1
items:
  tool:
    files:
      - {{path: tool.txt, target: ~/tool.txt, mode: copy}}
    steps:
      - name: setup
        check: {{path: {json.dumps(str(probe.mark))}}}
        script: setup.sh
"""
    remote = make_remote(packages={"tool": package}, files={"tool.txt": "tool\n"})
    script = remote.work / "setup.sh"
    script.write_text(f'#!/bin/sh\necho v1 > "{probe.mark}"\n')
    script.chmod(0o755)
    publish(remote, {})
    subscribe(remote.url, "--add", "tool")
    as_json(["add", "tool", "--policy", "sync"])
    assert _apply("--yes")[0] == 0
    probe.mark.unlink()
    (tmp_path / "clones/dotfiles/tool.txt").write_text("local edit\n")
    publish(remote, {"setup.sh": f'#!/bin/sh\necho v2 > "{probe.mark}"\n'})
    code, rows, stderr = _apply("--yes")
    assert code == 1, stderr
    assert rows[0]["action"] == "failed" and "local changes" in str(rows[0]["detail"]), stderr
    assert not probe.mark.exists()  # neither the old script nor the new one ran


@pytest.mark.skipif(sys.platform == "win32", reason="a script with a shebang")
def test_a_script_edited_in_the_clone_never_runs(
    make_remote: MakeRemote, probe: Probe, tmp_path: Path
) -> None:
    package = f"""\
version: 1
items:
  tool:
    steps:
      - name: setup
        check: {{path: {json.dumps(str(probe.mark))}}}
        script: setup.sh
"""
    remote = make_remote(packages={"tool": package}, files={})
    script = remote.work / "setup.sh"
    script.write_text(f'#!/bin/sh\necho source > "{probe.mark}"\n')
    script.chmod(0o755)
    publish(remote, {})
    subscribe(remote.url, "--add", "tool")
    (tmp_path / "clones/dotfiles/setup.sh").write_text(f'#!/bin/sh\necho local > "{probe.mark}"\n')
    code, rows, _ = _apply("--yes")
    assert code == 1
    assert "isn't the one the preview showed" in str(rows[0]["detail"])
    assert not probe.mark.exists()


def test_a_failed_step_before_an_applied_one_stays_pending(
    make_remote: MakeRemote, probe: Probe
) -> None:
    broken = (
        "      - name: first\n"
        f"        check: {{command: {probe.argv('check')}}}\n"
        f"        run: {probe.argv('broken')}\n"
    )
    package = _tool(probe).replace("    steps:\n", "    steps:\n" + broken)
    subscribe(_source(make_remote, package).url, "--add", "tool")
    code, rows, _ = _apply("--yes")
    assert code == 1
    actions = [(r["entry"], r["action"]) for r in rows]
    assert actions == [("first", "failed"), ("install", "created")]
    found = states()
    assert (found["first"], found["install"]) == ("pending", "applied")
