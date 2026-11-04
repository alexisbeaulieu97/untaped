"""``show``'s screen, driven key by key: what it changes, and that it saves once."""

from __future__ import annotations

from untaped.sdk.testing import drive_screen
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.records import ItemDetail, PackageDetail
from untaped_dotfiles.screen import Choices, PackageView, ShowResult, show_screen
from untaped_dotfiles.settings import ItemChoice, PackageChoice
from untaped_values.api import Input

AT = ("dotfiles", "git")


def _view(refused: str | None = None) -> PackageView:
    detail = PackageDetail(
        source="dotfiles",
        name="git",
        on=False,
        suggested=Policy.SYNC,
        description="Global gitignore and identity",
        items=(ItemDetail(name="ignore", enabled=True), ItemDetail(name="identity", enabled=True)),
    )
    inputs = {
        "git_email": Input(description="Email on commits"),
        "token": Input(description="A token", secret=True),
        "jobs": Input(description="How many at once", type="int"),
    }
    return PackageView(detail=detail, spelled="git", refused=refused, inputs=inputs)


def _drive(
    keys: list[str],
    choice: PackageChoice | None = None,
    *,
    refused: str | None = None,
    size: tuple[int, int] = (110, 24),
) -> tuple[ShowResult | None, list[Choices], str]:
    saved: list[Choices] = []
    screen = show_screen(
        [_view(refused)],
        {AT: choice},
        save=lambda result: saved.append(result.changes),
        command="untaped dotfiles show",
        alternative="untaped dotfiles show --format json",
    )
    ran = drive_screen(screen, keys, size=size)
    return ran.result, saved, ran.frame


def test_toggle_cycle_and_edit_save_once() -> None:
    keys = ["down", " ", "p", "right", "down", "e", *"a@example.com", "enter", "ctrl-s"]
    result, saved, _ = _drive(keys)
    want = PackageChoice(policy=Policy.SYNC, inputs={"git_email": "a@example.com"})
    assert saved == [{AT: want}]
    assert result is not None and result.changes == {AT: want}
    assert result.lines == ("turned on git (sync)", "set git input git_email")
    assert result.turned_on == (AT,)


def test_esc_closes_saving_nothing() -> None:
    result, saved, _ = _drive(["down", " ", "p", "esc"])
    assert saved == [] and result == ShowResult()


def test_an_item_turned_on_in_an_off_package_turns_only_it_on() -> None:
    identity = ["down", "right", *["down"] * 5]  # past the package's three inputs and ignore
    result, saved, _ = _drive([*identity, " ", "ctrl-s"])
    want = PackageChoice(items={"ignore": ItemChoice(enabled=False)})
    assert saved == [{AT: want}]
    assert result is not None and result.lines == (
        "turned on git",
        "turned off git/ignore",
    )


def test_an_item_and_a_package_turned_off() -> None:
    on = PackageChoice(policy=Policy.ONCE)
    ignore = ["down", "right", *["down"] * 4]
    result, saved, _ = _drive([*ignore, " ", "p", "ctrl-s"], on)
    want = PackageChoice(policy=Policy.ONCE, items={"ignore": ItemChoice(enabled=False)})
    assert saved == [{AT: want}]
    assert result is not None and result.lines == ("turned off git/ignore",)
    result, saved, _ = _drive(["down", " ", "ctrl-s"], on)
    assert saved == [{AT: None}]
    assert result is not None and result.lines == ("turned off git",)


def test_a_secret_input_is_not_edited() -> None:
    _, saved, frame = _drive(["down", "right", "down", "down", "e"], PackageChoice())
    assert "token is a secret: it is never kept in config.yml" in frame
    assert saved == []


def test_a_bad_answer_stays_in_the_field_until_it_is_fixed() -> None:
    keys = ["down", "right", "down", "down", "down", "e", *"many", "enter"]
    _, saved, frame = _drive([*keys, "ctrl-s"], PackageChoice())
    assert "input 'jobs'" in frame and saved == []
    fixed = [*keys, *["backspace"] * 4, *"4", "enter", "ctrl-s"]
    result, saved, _ = _drive(fixed, PackageChoice())
    assert saved == [{AT: PackageChoice(inputs={"jobs": 4})}]
    assert result is not None and result.lines == ("set git input jobs",)


def test_a_package_that_does_not_apply_stays_off() -> None:
    result, saved, frame = _drive(["down", " "], refused="macos only")
    assert "git can't be turned on here: macos only" in frame
    assert saved == [] and result is None


def test_nothing_changed_saves_nothing() -> None:
    result, saved, _ = _drive(["down", " ", " ", "ctrl-s"])
    assert saved == [] and result == ShowResult()


def test_an_item_policy_chosen_then_turned_off_is_still_said() -> None:
    off = PackageChoice(items={"ignore": ItemChoice(enabled=False)})
    ignore = ["down", "right", *["down"] * 4]
    result, saved, _ = _drive([*ignore, " ", "p", " ", "ctrl-s"], off)
    assert saved == [
        {AT: PackageChoice(items={"ignore": ItemChoice(enabled=False, policy=Policy.SYNC)})}
    ]
    assert result is not None and result.lines == ("policy of git/ignore: sync",)
    result, _, _ = _drive([*ignore, " ", "p", "p", "p", "p", "ctrl-s"], off)
    assert result is not None and result.lines == ("turned on git/ignore",)


def test_turned_off_is_listed_for_the_command_to_say_what_stays() -> None:
    on = PackageChoice()
    result, _, _ = _drive(["down", "right", *["down"] * 4, " ", "ctrl-s"], on)
    assert result is not None and result.turned_off == ((*AT, "ignore"),)
    result, _, _ = _drive(["down", " ", "ctrl-s"], on)
    assert result is not None and result.turned_off == (AT,)


def test_keys_while_saving_wait_for_the_write() -> None:
    saved: list[ShowResult] = []
    screen = show_screen(
        [_view()],
        {AT: None},
        save=saved.append,
        command="untaped dotfiles show",
        alternative="untaped dotfiles show --format json",
    )
    ran = drive_screen(screen, ["down", " ", "ctrl-s", "esc", "ctrl-c"], commands={"save": None})
    assert ran.result is None and "saving; it closes when done" in ran.frame


def test_a_narrow_terminal_still_shows_the_input_being_edited() -> None:
    keys = ["down", " ", "right", "down", "e", *"abc"]
    _, _, frame = _drive(keys, size=(80, 24))
    assert "abc" in frame
    _, _, frame = _drive(["down", " "], refused="macos only", size=(80, 24))
    assert "can't be turned on here: macos only" in frame
