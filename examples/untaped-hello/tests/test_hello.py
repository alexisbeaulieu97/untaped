"""The example plugin, tested the way any third-party plugin would be."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from untaped.sdk import Progress, StaleData
from untaped.testing import cache_clock, check_conventions, invoke_root


def test_hello_follows_the_conventions() -> None:
    check_conventions("hello")


def test_hello_loads_lazily() -> None:
    """A fresh interpreter: rendering the root help never imports the CLI."""
    code = (
        "import sys; from untaped.testing import invoke_root; "
        "r = invoke_root(['--help']); "
        "print(r.exit_code, 'hello' in r.stdout, 'untaped_hello.cli' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["0", "True", "False"]


def test_hello_is_listed_ready() -> None:
    rows = json.loads(invoke_root(["plugin", "list", "--format", "json"]).stdout)
    assert [(r["name"], r["status"]) for r in rows] == [("hello", "ready")]


def test_hello_greets_with_its_setting() -> None:
    result = invoke_root(["hello", "greet"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello from untaped-hello"


def test_hello_greets_with_a_configured_greeting() -> None:
    assert invoke_root(["config", "set", "hello.greeting", "hi"]).exit_code == 0
    result = invoke_root(["hello", "greet"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "hi"


def test_hello_moves_its_old_greetings_with_migrate_dirs() -> None:
    old = Path.home() / ".untaped" / "hello-greetings"
    old.mkdir(parents=True)
    (old / "fr.txt").write_text("bonjour", encoding="utf-8")

    preview = invoke_root(["setup", "migrate-dirs", "--dry-run", "--format", "json"])
    assert [(r["id"], r["action"]) for r in json.loads(preview.stdout)] == [
        ("hello.greetings", "move")
    ]

    applied = invoke_root(["setup", "migrate-dirs", "--yes", "--format", "json"])
    assert applied.exit_code == 0, applied.stderr
    assert [r["action"] for r in json.loads(applied.stdout)] == ["moved"]
    moved = Path.home() / ".untaped" / "plugins" / "hello" / "greetings" / "fr.txt"
    assert moved.read_text(encoding="utf-8") == "bonjour"


def test_hello_names_the_setting_while_the_greeting_is_the_default() -> None:
    from untaped_hello.cli import DefaultGreeting

    result = invoke_root(["hello", "greet"])
    assert [type(each) for each in result.diagnostics] == [DefaultGreeting]
    assert result.stderr.startswith("hint: this is the default greeting\n")

    assert invoke_root(["config", "set", "hello.greeting", "hi"]).exit_code == 0
    assert invoke_root(["hello", "greet"]).diagnostics == ()


def test_hello_s_own_kind_has_a_schema() -> None:
    result = invoke_root(["plugin", "schema", "hello.default_greeting"])
    assert result.exit_code == 0, result.stderr
    assert "setting" in json.loads(result.stdout)["properties"]


def _motd(tmp_path: Path, text: str = "ship it\n") -> Path:
    source = tmp_path / "motd.txt"
    source.write_text(text, encoding="utf-8")
    assert invoke_root(["config", "set", "hello.motd_path", str(source)]).exit_code == 0
    return source


def test_hello_motd_fills_its_cache_on_first_use_then_reads_it(tmp_path: Path) -> None:
    source = _motd(tmp_path)
    with cache_clock() as clock:
        first = invoke_root(["hello", "motd"])
        assert (first.exit_code, first.stdout) == (0, "ship it\n")
        source.write_text("changed\n", encoding="utf-8")
        clock.advance(timedelta(minutes=30))

        fresh = invoke_root(["hello", "motd"])
        assert (fresh.stdout, fresh.diagnostics) == ("ship it\n", ())

        clock.advance(timedelta(hours=1))
        assert invoke_root(["hello", "motd"]).stdout == "changed\n"


def test_hello_motd_shows_its_refresh_under_verbose(tmp_path: Path) -> None:
    _motd(tmp_path)
    result = invoke_root(["--verbose", "hello", "motd"])
    progress = [each for each in result.diagnostics if isinstance(each, Progress)]
    assert progress[0].label.startswith("Refreshing ") and progress[0].label.endswith("(first use)")
    assert progress[-1].state == "done"
    assert progress[-1].message.split(" · ")[-1].startswith("1 line")


def test_hello_motd_cached_with_nothing_cached_fails(tmp_path: Path) -> None:
    _motd(tmp_path)
    result = invoke_root(["hello", "motd", "--cached"])
    assert result.exit_code == 1
    assert "no cached message of the day for" in result.stderr
    assert "drop --cached to refresh it" in result.stderr


def test_hello_motd_serves_its_copy_when_the_file_is_gone(tmp_path: Path) -> None:
    source = _motd(tmp_path)
    with cache_clock() as clock:
        assert invoke_root(["hello", "motd"]).exit_code == 0
        source.unlink()
        clock.advance(timedelta(hours=2))

        result = invoke_root(["hello", "motd"])
    assert (result.exit_code, result.stdout) == (0, "ship it\n")
    [stale] = result.diagnostics
    assert isinstance(stale, StaleData)
    assert (stale.plugin, stale.cache, stale.key) == ("hello", "hello.motd", str(source.resolve()))
    assert "refresh failed (could not read" in stale.message


def test_hello_motd_keeps_one_copy_per_file(tmp_path: Path) -> None:
    """Pointing ``hello.motd_path`` elsewhere never serves the other file's copy."""
    first = _motd(tmp_path)
    other = tmp_path / "other.txt"
    other.write_text("other\n", encoding="utf-8")
    with cache_clock():
        assert invoke_root(["hello", "motd"]).stdout == "ship it\n"
        invoke_root(["config", "set", "hello.motd_path", str(other)])
        assert invoke_root(["hello", "motd"]).stdout == "other\n"
        invoke_root(["config", "set", "hello.motd_path", str(first)])
        assert invoke_root(["hello", "motd"]).stdout == "ship it\n"


def test_hello_motd_refuses_refresh_and_cached_together(tmp_path: Path) -> None:
    _motd(tmp_path)
    assert invoke_root(["hello", "motd", "--refresh", "--cached"]).exit_code == 2


def test_hello_motd_reads_the_file_every_time_at_max_age_zero(tmp_path: Path) -> None:
    source = _motd(tmp_path)
    assert invoke_root(["config", "set", "hello.caches.motd.max_age_seconds", "0"]).exit_code == 0
    with cache_clock() as clock:
        assert invoke_root(["hello", "motd"]).stdout == "ship it\n"
        source.write_text("changed\n", encoding="utf-8")
        clock.advance(timedelta(seconds=1))
        assert invoke_root(["hello", "motd"]).stdout == "changed\n"
