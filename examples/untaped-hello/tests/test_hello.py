"""The example plugin, tested the way any third-party plugin would be.

``install(Hello)`` composes the plugin into the test's own home; ``.run``
runs a built command, ``.cli`` a command line, and both return what it did.
``clock`` is the one clock the plugin's cache reads.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from untaped_hello.cli import DefaultGreeting, Greet, Motd, Say
from untaped_hello.plugin import Hello
from untaped_hello.records import Salutation
from untaped_hello.settings import HelloSettings

from untaped.sdk import Progress, StaleData
from untaped.sdk.testing import Home, Install, ManualClock
from untaped_lint.testing import assert_clean

Installer = Callable[..., Install]


def test_hello_passes_lint() -> None:
    assert_clean("hello")


def test_hello_loads_lazily() -> None:
    """A fresh interpreter after the first start: rendering the root help never imports the CLI.

    The first start composes the plugin class, which imports its commands,
    and core keeps what it composed; every start after that reads it.
    """
    code = (
        "import sys; from untaped.sdk.testing import invoke_root; "
        "r = invoke_root(['--help']); "
        "print(r.exit_code, 'hello' in r.stdout, 'untaped_hello.cli' in sys.modules)"
    )
    first = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert first.stdout.split()[:2] == ["0", "True"]
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["0", "True", "False"]


def test_hello_is_listed_ready(install: Installer) -> None:
    listed = install(Hello).cli("plugin list --format json")
    assert {r["name"]: r["status"] for r in json.loads(listed.stdout)}["hello"] == "ready"


def test_hello_greets_with_its_setting(install: Installer) -> None:
    ran = install(Hello).run(Greet())
    assert (ran.exit, ran.text) == (0, "hello from untaped-hello")


def test_hello_greets_with_a_configured_greeting(install: Installer) -> None:
    ran = install(Hello, settings=HelloSettings(greeting="hi")).run(Greet())
    assert (ran.exit, ran.text, ran.diagnostics) == (0, "hi", ())


def test_hello_names_the_setting_while_the_greeting_is_the_default(install: Installer) -> None:
    ran = install(Hello).run(Greet())
    assert [type(each) for each in ran.diagnostics] == [DefaultGreeting]
    assert ran.stderr[0] == "hint: this is the default greeting"


def test_hello_say_greets_each_name(install: Installer) -> None:
    ran = install(Hello, settings=HelloSettings(greeting="hi")).run(Say(names=["ada", "grace"]))
    assert ran.rows_as(Salutation) == [
        Salutation(name="ada", text="hi, ada"),
        Salutation(name="grace", text="hi, grace"),
    ]


def test_hello_say_pipes_its_greetings_back_in_by_their_model(install: Installer) -> None:
    hello = install(Hello)
    piped = hello.cli("hello say ada grace --format pipe")
    assert piped.exit == 0, piped.stderr
    assert {json.loads(line)["kind"] for line in piped.stdout.splitlines()} == {"hello.salutation"}

    again = hello.cli("hello say --stdin --format json", input=piped.stdout)
    assert [row.name for row in again.rows_as(Salutation)] == ["ada", "grace"]
    assert hello.run(Say(), stdin=again.rows).rows_as(Salutation) == again.rows


def test_hello_say_refuses_records_of_another_kind(install: Installer) -> None:
    hello = install(Hello)
    plugins = hello.cli("plugin list --format pipe").stdout

    ran = hello.cli("hello say --stdin", input=plugins)

    assert ran.exit == 2
    assert any("expected 'hello.salutation'" in line for line in ran.stderr)


def test_hello_moves_its_old_greetings_with_migrate_dirs(install: Installer, home: Home) -> None:
    old = home.path / ".untaped" / "hello-greetings"
    old.mkdir(parents=True)
    (old / "fr.txt").write_text("bonjour", encoding="utf-8")
    hello = install(Hello)

    preview = hello.cli("setup migrate-dirs --dry-run --format json")
    assert [(r["id"], r["action"]) for r in json.loads(preview.stdout)] == [
        ("hello.greetings", "move")
    ]

    applied = hello.cli("setup migrate-dirs --yes --format json")
    assert applied.exit == 0, applied.stderr
    assert [r["action"] for r in json.loads(applied.stdout)] == ["moved"]
    assert (home.plugin(Hello) / "greetings" / "fr.txt").read_text(encoding="utf-8") == "bonjour"


def test_hello_s_own_kind_has_a_schema(install: Installer) -> None:
    ran = install(Hello).cli("plugin schema hello.default_greeting")
    assert ran.exit == 0, ran.stderr
    assert "setting" in json.loads(ran.stdout)["properties"]


def _motd(tmp_path: Path, text: str = "ship it\n") -> Path:
    source = tmp_path / "motd.txt"
    source.write_text(text, encoding="utf-8")
    return source


def test_hello_motd_fills_its_cache_on_first_use_then_reads_it(
    install: Installer, clock: ManualClock, tmp_path: Path
) -> None:
    source = _motd(tmp_path)
    hello = install(Hello, settings=HelloSettings(motd_path=source))
    first = hello.run(Motd())
    assert (first.exit, first.text) == (0, "ship it")
    source.write_text("changed\n", encoding="utf-8")
    clock.advance(minutes=30)

    fresh = hello.run(Motd())
    assert (fresh.text, fresh.diagnostics) == ("ship it", ())

    clock.advance(hours=1)
    assert hello.run(Motd()).text == "changed"


def test_hello_motd_shows_its_refresh_under_verbose(install: Installer, tmp_path: Path) -> None:
    ran = install(Hello, settings=HelloSettings(motd_path=_motd(tmp_path))).cli(
        "--verbose hello motd"
    )
    progress = [each for each in ran.diagnostics if isinstance(each, Progress)]
    assert progress[0].label.startswith("Refreshing ") and progress[0].label.endswith("(first use)")
    assert progress[-1].state == "done"
    assert progress[-1].message.split(" · ")[-1].startswith("1 line")


def test_hello_motd_cached_with_nothing_cached_fails(install: Installer, tmp_path: Path) -> None:
    ran = install(Hello, settings=HelloSettings(motd_path=_motd(tmp_path))).run(
        Motd(), refresh=False
    )
    assert ran.exit == 1
    assert "no cached message of the day for" in "\n".join(ran.stderr)
    assert "drop --cached to refresh it" in "\n".join(ran.stderr)


def test_hello_motd_serves_its_copy_when_the_file_is_gone(
    install: Installer, clock: ManualClock, tmp_path: Path
) -> None:
    source = _motd(tmp_path)
    hello = install(Hello, settings=HelloSettings(motd_path=source))
    assert hello.run(Motd()).exit == 0
    source.unlink()
    clock.advance(hours=2)

    ran = hello.run(Motd())
    assert (ran.exit, ran.text) == (0, "ship it")
    [stale] = ran.diagnostics
    assert isinstance(stale, StaleData)
    assert (stale.plugin, stale.cache, stale.key) == ("hello", "hello.motd", str(source.resolve()))
    assert "refresh failed (could not read" in stale.message


def test_hello_motd_keeps_one_copy_per_file(install: Installer, tmp_path: Path) -> None:
    """Pointing ``hello.motd_path`` elsewhere never serves the other file's copy."""
    first = _motd(tmp_path)
    other = tmp_path / "other.txt"
    other.write_text("other\n", encoding="utf-8")
    assert install(Hello, settings=HelloSettings(motd_path=first)).run(Motd()).text == "ship it"
    assert install(Hello, settings=HelloSettings(motd_path=other)).run(Motd()).text == "other"
    assert install(Hello, settings=HelloSettings(motd_path=first)).run(Motd()).text == "ship it"


def test_hello_motd_refuses_refresh_and_cached_together(install: Installer, tmp_path: Path) -> None:
    hello = install(Hello, settings=HelloSettings(motd_path=_motd(tmp_path)))
    assert hello.cli("hello motd --refresh --cached").exit == 2


def test_hello_motd_reads_the_file_every_time_at_max_age_zero(
    install: Installer, clock: ManualClock, tmp_path: Path
) -> None:
    source = _motd(tmp_path)
    hello = install(Hello, settings=HelloSettings(motd_path=source))
    assert hello.cli("config set hello.caches.motd.max_age_seconds 0").exit == 0
    assert hello.run(Motd()).text == "ship it"
    source.write_text("changed\n", encoding="utf-8")
    clock.advance(seconds=1)
    assert hello.run(Motd()).text == "changed"
