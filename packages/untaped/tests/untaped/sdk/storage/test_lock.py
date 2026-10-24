"""Lock: an operation lock on a plugin class, held across processes, re-entrant in a thread."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from filelock import FileLock
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.sdk import ErrorCategory, Lock, Plugin, UntapedError
from untaped.sdk.run import NoRunError, Run, run_scope


class Settings(BaseModel):
    pass


class Demo(Plugin):
    """Demo."""

    settings = Settings
    sync = Lock(wait=timedelta(0))


@pytest.fixture(autouse=True)
def run(tmp_path: Path) -> Iterator[Run]:
    with run_scope(run_owning(tmp_path, __name__.partition(".")[0], "demo")) as active:
        yield active


def test_the_lock_is_a_file_in_the_plugins_folder(tmp_path: Path) -> None:
    with Demo.sync.locked():
        assert (tmp_path / "plugins" / "demo" / "sync.lock").exists()
    assert Demo.sync.path == tmp_path / "plugins" / "demo" / "sync.lock"


def test_taking_it_again_in_one_thread_only_counts() -> None:
    with Demo.sync.locked(), Demo.sync.locked(), Demo.sync.try_locked() as held:
        assert held


def test_another_holder_makes_it_busy(tmp_path: Path) -> None:
    path = tmp_path / "plugins" / "demo" / "sync.lock"
    path.parent.mkdir(parents=True)
    other = FileLock(str(path))
    with other:
        with Demo.sync.try_locked() as held:
            assert not held
        with (
            pytest.raises(
                UntapedError, match=r"demo\.sync is busy \(another untaped process\)"
            ) as caught,
            Demo.sync.locked(),
        ):
            pytest.fail("held while another process holds it")
        assert caught.value.category == ErrorCategory.UNAVAILABLE
    with Demo.sync.try_locked() as held:
        assert held


def test_an_error_inside_the_block_passes_through() -> None:
    with pytest.raises(UntapedError, match="boom") as caught, Demo.sync.try_locked():
        raise UntapedError("boom", category=ErrorCategory.UNAVAILABLE)
    assert str(caught.value) == "boom"


@pytest.mark.parametrize("wait", [timedelta(seconds=-1), 10])
def test_the_wait_is_a_timedelta_of_zero_or_more(wait: object) -> None:
    with pytest.raises(TypeError, match="wait takes a timedelta"):
        Lock(wait=wait)  # type: ignore[arg-type]


def test_it_reads_as_written() -> None:
    assert repr(Demo.sync) == f"Lock('Lock created in {__name__}', wait=datetime.timedelta(0))"


def test_another_thread_holding_it_makes_it_busy() -> None:
    import contextvars
    import threading

    taken, release = threading.Event(), threading.Event()

    def hold() -> None:
        with Demo.sync.locked():
            taken.set()
            release.wait(5)

    worker = threading.Thread(target=contextvars.copy_context().run, args=(hold,))
    worker.start()
    taken.wait(5)
    try:
        with Demo.sync.try_locked() as held:
            assert not held
    finally:
        release.set()
        worker.join(5)


def test_outside_a_run_it_says_so() -> None:
    with run_scope(None), pytest.raises(NoRunError), Demo.sync.locked():  # type: ignore[arg-type]
        pytest.fail("held outside a run")
