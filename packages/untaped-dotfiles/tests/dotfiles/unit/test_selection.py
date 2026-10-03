"""Per-machine selection: os, tags, skips, and the mode a policy places a file with."""

from __future__ import annotations

from untaped_dotfiles.domain.manifest import FileEntry, Item
from untaped_dotfiles.domain.models import Machine
from untaped_dotfiles.domain.selection import (
    effective_mode,
    file_exclusion,
    item_exclusion,
    selected_files,
)

LINUX = Machine(os="linux", tags=frozenset({"work"}))
MAC = Machine(os="macos", tags=frozenset({"claude-plugin-dev"}))


def _item(**kwargs: object) -> Item:
    files = kwargs.pop("files", (FileEntry(source="a", target="~/a"),))
    return Item(files=files, **kwargs)  # type: ignore[arg-type]


def test_an_item_without_filters_applies_everywhere() -> None:
    assert item_exclusion(_item(), LINUX) is None
    assert item_exclusion(_item(), MAC) is None


def test_os_filters_the_item() -> None:
    assert item_exclusion(_item(os=("macos",)), LINUX) == "os is linux, not macos"
    assert item_exclusion(_item(os=("macos", "linux")), LINUX) is None


def test_only_and_unless_match_the_machine_tags() -> None:
    assert item_exclusion(_item(only=("work",)), LINUX) is None
    assert item_exclusion(_item(only=("home",)), LINUX) == "needs tag home"
    assert item_exclusion(_item(unless=("work",)), LINUX) == "excluded by tag work"
    assert item_exclusion(_item(unless=("work",)), MAC) is None


def test_file_filters_and_skips_apply_after_the_item() -> None:
    settings = FileEntry(source="s.json", target="~/s.json", unless=("claude-plugin-dev",))
    skills = FileEntry(source="skills", target="~/skills", name="skills")
    item = _item(files=(settings, skills))
    assert file_exclusion(item, settings, MAC) == "excluded by tag claude-plugin-dev"
    assert file_exclusion(item, settings, LINUX) is None
    assert file_exclusion(item, skills, LINUX, skip=("skills",)) == "skipped on this machine"
    excluded = _item(files=(settings,), os=("macos",))
    assert file_exclusion(excluded, settings, LINUX) == "os is linux, not macos"
    assert [reason for _, reason in selected_files(item, MAC)] == [
        "excluded by tag claude-plugin-dev",
        None,
    ]


def test_once_places_link_files_as_copies() -> None:
    assert effective_mode("link", "once") == "copy"
    assert effective_mode("link", "sync") == "link"
    assert effective_mode("link", "manual") == "link"
    assert effective_mode("merge", "once") == "merge"
    assert effective_mode("copy", "once") == "copy"
