"""The step runner: only approved steps run, with no shell, in the home, masked."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from untaped_dotfiles import step_runner
from untaped_dotfiles.errors import DotfilesError
from untaped_dotfiles.models import StepRecord
from untaped_dotfiles.package import PackageInput, Step
from untaped_dotfiles.step_runner import ApprovedStep, StepRunner, step_digest

T0 = datetime(2026, 11, 3, tzinfo=UTC)


def _step(**fields: object) -> Step:
    return Step.model_validate({"name": "s", **fields})


def _approved(step: Step, values: dict[str, object] | None = None) -> ApprovedStep:
    specs = {name: PackageInput() for name in values or {}}
    return ApprovedStep.from_plan(
        "src/pkg/item/s", step, confirmed=True, specs=specs, values=values or {}, script=None
    )


def test_an_approved_step_comes_only_from_its_two_constructors() -> None:
    step = _step(check={"program": "git"}, fix="install git")
    with pytest.raises(TypeError, match="steps row or a confirmed plan"):
        ApprovedStep("k", step, {}, {})
    with pytest.raises(TypeError, match="only a confirmed plan"):
        ApprovedStep.from_plan(
            "k",
            step,
            confirmed=False,  # type: ignore[arg-type]
            specs={},
            values={},
            script=None,
        )
    record = StepRecord(
        step="src/pkg/item/s",
        declared=step,
        digest=step_digest(step, None),
        applied_at=T0,
        checked_at=T0,
    )
    assert ApprovedStep.from_record(record, specs={}, script=None).step == step


def test_the_digest_covers_the_step_and_its_script() -> None:
    step = _step(check={"program": "git"}, fix="install git")
    assert step_digest(step, None) != step_digest(step, "sha256:1")
    other = _step(check={"program": "git"}, fix="install it")
    assert step_digest(step, None) != step_digest(other, None)


def test_a_path_check_reads_the_home(tmp_path: Path) -> None:
    runner = StepRunner(home=tmp_path)
    step = _step(check={"path": "~/marker"}, fix="make it")
    assert runner.check(_approved(step)).detail == f"{tmp_path / 'marker'} is absent"
    (tmp_path / "marker").touch()
    assert runner.check(_approved(step)).passed


def test_a_program_check_looks_on_path(tmp_path: Path) -> None:
    runner = StepRunner(home=tmp_path)
    assert not runner.check(_approved(_step(check={"program": "no-such-tool-x"}, fix="f"))).passed
    found = Path(sys.executable).name
    assert runner.check(_approved(_step(check={"program": found}, fix="f"))).passed


def test_a_command_check_compares_what_it_prints(tmp_path: Path) -> None:
    runner = StepRunner(home=tmp_path)
    printing = [sys.executable, "-c", "import os; print(os.getcwd())"]
    step = _step(check={"command": printing, "prints": "{{ where }}"}, fix="f")
    assert runner.check(_approved(step, {"where": str(tmp_path)})).passed  # cwd is the home
    checked = runner.check(_approved(step, {"where": "/elsewhere"}))
    assert not checked.passed and "doesn't print '/elsewhere'" in checked.detail
    failing = _step(check={"command": [sys.executable, "-c", "raise SystemExit(2)"]}, fix="f")
    assert runner.check(_approved(failing)).detail.endswith("exits 2")


def test_a_program_that_cant_start_fails_with_its_name(tmp_path: Path) -> None:
    runner = StepRunner(home=tmp_path)
    step = _step(check={"command": ["no-such-tool-x"]}, fix="f")
    with pytest.raises(DotfilesError, match="cannot start 'no-such-tool-x'"):
        runner.check(_approved(step))


def test_a_check_that_takes_too_long_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(step_runner, "CHECK_TIMEOUT", 0.2)
    runner = StepRunner(home=tmp_path)
    step = _step(check={"command": [sys.executable, "-c", "import time; time.sleep(5)"]}, fix="f")
    with pytest.raises(DotfilesError, match="took over 0 s"):
        runner.check(_approved(step))


def test_a_run_relays_its_output_with_secrets_masked(tmp_path: Path) -> None:
    lines: list[str] = []
    runner = StepRunner(home=tmp_path).with_relay(lines.append)
    code = "import sys; d = sys.stdin.read(); print('out', d); print('err', d, file=sys.stderr)"
    step = _step(check={"program": "x"}, run=[sys.executable, "-c", code])
    runner.run(_approved(step), stdin=b"hush", secrets=["hush"])
    assert lines == ["out ***", "err ***"]
    broken = _step(check={"program": "x"}, run=[sys.executable, "-c", "raise SystemExit(5)"])
    with pytest.raises(DotfilesError, match="exits 5"):
        runner.run(_approved(broken), stdin=None, secrets=[])


def test_a_report_only_step_has_nothing_to_run() -> None:
    assert _approved(_step(check={"program": "git"}, fix="install git")).argv is None
