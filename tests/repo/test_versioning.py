"""``docs/versioning.md`` names exactly the experimental things the marks say.

The bullet list under "## Experimental" is kept by hand, because it also names
record kinds, file formats and environment variables no mark knows. This
checks it rather than generating it: every experimental capability, group or
command is a bullet, and every bullet that starts with a command path is marked.
"""

from __future__ import annotations

import re

from repo.support import REPO_ROOT
from untaped import bootstrap
from untaped.capabilities.registry import ProviderCandidate
from untaped.stability import Experimental, marks

_BULLET = re.compile(r"^- `([^`]+)`:", re.MULTILINE)


def experimental_section(text: str) -> str:
    """The body of the ``## Experimental`` section of ``docs/versioning.md``."""
    match = re.search(r"^## Experimental\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match is not None, "docs/versioning.md has no '## Experimental' section"
    return match.group(1)


def bullet_paths(text: str) -> set[str]:
    """The command paths the bullets of ``## Experimental`` start with (``awx test``)."""
    return set(_BULLET.findall(experimental_section(text)))


def versioning_problems(text: str, marked: set[str]) -> list[str]:
    """What is missing from, and what is stray on, the ``## Experimental`` bullets."""
    listed = bullet_paths(text)
    return [
        *(f"{path}: experimental but has no bullet" for path in sorted(marked - listed)),
        *(
            f"{path}: has a bullet but is not marked experimental"
            for path in sorted(listed - marked)
        ),
    ]


def _bullets(*paths: str) -> str:
    return (
        "## Experimental\n\n" + "".join(f"- `{path}`: things.\n" for path in paths) + "\n## Next\n"
    )


def test_a_missing_bullet_and_a_stray_bullet_each_fail() -> None:
    assert versioning_problems(_bullets("awx test"), {"awx test", "dotfiles"}) == [
        "dotfiles: experimental but has no bullet"
    ]
    assert versioning_problems(_bullets("awx test", "gone"), {"awx test"}) == [
        "gone: has a bullet but is not marked experimental"
    ]
    assert versioning_problems(_bullets("awx test"), {"awx test"}) == []


def test_only_the_experimental_section_counts() -> None:
    text = "## Before\n\n- `other`: not here.\n\n" + _bullets("awx test")

    assert bullet_paths(text) == {"awx test"}


def test_every_experimental_mark_of_the_repo_is_on_the_versioning_page(
    first_party_candidates: tuple[ProviderCandidate, ...], fresh_composition: None
) -> None:
    root = bootstrap.build_root_app(candidates=first_party_candidates)
    marked = {
        mark.where
        for mark in marks(root, bootstrap.composition(), resolve=True)
        if isinstance(mark.stability, Experimental)
    }
    text = (REPO_ROOT / "docs" / "versioning.md").read_text(encoding="utf-8")

    assert marked >= {"awx test", "workspace", "dotfiles"}
    assert versioning_problems(text, marked) == []
