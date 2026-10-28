"""``untaped hello motd``: ``clock`` is the one clock the plugin's cache reads."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from untaped_hello.commands.motd import Motd
from untaped_hello.plugin import Hello
from untaped_hello.settings import HelloSettings

from untaped.sdk import Progress, Stale
from untaped.sdk.testing import Install, ManualClock

Installer = Callable[..., Install]


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
    assert isinstance(stale, Stale)
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
