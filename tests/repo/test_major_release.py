"""Every deprecation is a promise with a date, kept: ``tests/repo/surface.txt`` against its rules.

The limit is the major of the version main carries (``X.0.0rcN`` counts as
``X``): main carries the last release, so between releases the limit is the
released major, and from a major's first release candidate on it is the major
being released.

1. **Overdue.** Nothing may be marked removed in the limit or earlier: the
   first PR after a release finds a leftover, and a major's release PR is the
   backstop. ``scripts/gen_surface.py --due`` lists what the next major removes.
2. **Dated.** Every deprecated line of this workspace names its removal
   version (the six SDK lines in :data:`UNDATED` until #676 dates them).
3. **Kept as promised.** Against the newest final release's file, a date it
   showed never falls, and a line it dated never loses its mark while it
   stays. While a major is being released, something deprecated since that
   release (no release warned about it) may not be dated for it.

Rules 1 and 2 need no tags; rule 3 skips without one locally and fails in CI.
Removing a deprecated line at its major is ``test_surface.py``'s
``removal_exempt``.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
import release  # scripts/release.py
from packaging.version import Version

from repo.support import REPO_ROOT, SURFACE_FILE, git, reachable_tags, release_baseline
from untaped.git import GitCommandError
from untaped.surface import DeprecatedMark, Line, parse, postponements

#: Deprecated SDK lines released undated in 11.0; #676 dates them and deletes this.
UNDATED = frozenset(
    {
        "sdk untaped.sdk.UiContext.message(kind, text)",
        "sdk untaped.sdk.UiContext.progress(label)",
        "sdk untaped.sdk.UiContext.success(text)",
        "sdk untaped.sdk.report_error(exc)",
        "sdk untaped.sdk.report_error(item=) default=None",
        "sdk untaped.sdk.report_row_errors(rows, *, item) callback(item, 1)",
    }
)

POSTPONE = (
    "Remove each in a prep PR (`scripts/gen_surface.py --due` lists them), or postpone it on "
    "purpose: raise removed_in on its declaration, regenerate tests/repo/surface.txt and add a "
    "changelog.d/<slug>.deprecated.md fragment with the reason."
)


def limit_of(version: str) -> int:
    """The major ``version`` carries: what nothing may still be marked removed in."""
    return Version(version).major


def _current() -> list[Line]:
    # test_surface keeps the committed file equal to what the code generates.
    return parse((REPO_ROOT / SURFACE_FILE).read_text(encoding="utf-8"))


def overdue(
    current: Sequence[Line], limit: int, *, unwarned: frozenset[str] = frozenset()
) -> list[Line]:
    """Rule 1: lines marked removed in ``limit`` or earlier, except those rule 3 claims."""
    return sorted(
        line
        for line in current
        if (mark := line.deprecation) is not None
        and mark.removed_in is not None
        and mark.removed_in <= limit
        and line.identity not in unwarned
    )


def undated(current: Sequence[Line], allowed: frozenset[str] = UNDATED) -> list[Line]:
    """Rule 2: deprecated lines without a removal version, the allowlist aside."""
    return sorted(
        line
        for line in current
        if (mark := line.deprecation) is not None
        and mark.removed_in is None
        and line.identity not in allowed
    )


def unwarned_identities(baseline: Sequence[Line], current: Sequence[Line]) -> frozenset[str]:
    """The current deprecated lines the baseline does not mark: no release warned about them."""
    marked = {line.identity for line in baseline if line.deprecation is not None}
    return frozenset(
        line.identity
        for line in current
        if line.deprecation is not None and line.identity not in marked
    )


def broken_promises(
    baseline: Sequence[Line], current: Sequence[Line], *, tag: str, limit: int
) -> list[str]:
    """Rule 3: each way ``current`` breaks a promise ``tag``'s file showed, one message each."""
    found: list[str] = []
    for moved in postponements(baseline, current):
        if moved.now < moved.was:
            found.append(
                f"{moved.line}: {tag} promised {moved.was}.0; a shown promise never shortens"
            )
    dated = {
        line.identity: mark.removed_in
        for line in baseline
        if (mark := line.deprecation) is not None and mark.removed_in is not None
    }
    found.extend(
        f"{line}: {tag} promised users this goes in {dated[line.identity]}.0; "
        "keep the mark or remove the thing"
        for line in sorted(current)
        if line.identity in dated and line.deprecation is None
    )
    if limit > limit_of(tag.removeprefix("v")):
        unwarned = unwarned_identities(baseline, current)
        found.extend(
            f"{line}: deprecated since {tag}, the last release, so no release has warned about "
            f"it; a deprecation ships in a minor first; set removed_in={limit + 1}"
            for line in overdue(current, limit)
            if line.identity in unwarned
        )
    return found


def _baseline() -> tuple[str, list[Line]]:
    tag = release_baseline()
    try:
        return tag, parse(git("show", f"{tag}:{SURFACE_FILE}"))
    except GitCommandError:
        pytest.skip(f"{tag} predates {SURFACE_FILE}, so it promised nothing yet")


def _unwarned_while_releasing(current: Sequence[Line], limit: int) -> frozenset[str]:
    """Rule 3's claim on rule 1 while a major is being released; nothing without a tag."""
    tag = release.baseline_tag(reachable_tags())
    if tag is None or limit <= limit_of(tag.removeprefix("v")):
        return frozenset()
    try:
        return unwarned_identities(parse(git("show", f"{tag}:{SURFACE_FILE}")), current)
    except GitCommandError:
        return frozenset()


def test_nothing_overdue() -> None:
    version = release.release_version(REPO_ROOT)
    limit = limit_of(version)
    current = _current()
    found = overdue(current, limit, unwarned=_unwarned_while_releasing(current, limit))

    assert not found, (
        f"main is at {version}, so nothing may be marked removed in {limit}.0 or earlier; "
        "these are still here:\n" + "\n".join(f"  {line}" for line in found) + f"\n{POSTPONE}"
    )


def test_every_deprecation_is_dated() -> None:
    found = undated(_current())

    assert not found, (
        "every deprecation is a promise with a date; these name none (pass removed_in=<major> "
        "to their mark, then regenerate tests/repo/surface.txt):\n"
        + "\n".join(f"  {line}" for line in found)
    )


def test_promises_are_kept() -> None:
    tag, baseline = _baseline()
    limit = limit_of(release.release_version(REPO_ROOT))

    found = broken_promises(baseline, _current(), tag=tag, limit=limit)

    assert not found, "\n".join(found)


def test_the_undated_allowlist_is_still_needed() -> None:
    present = {line.identity for line in _current() if line.deprecation is not None}
    assert present >= UNDATED, f"dated or gone, drop from UNDATED: {sorted(UNDATED - present)}"


# --- the rules over small line sets ------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "limit"),
    [("11.1.0", 11), ("12.0.0rc1", 12), ("12.0.0", 12), ("12.0.1", 12)],
)
def test_the_limit_is_the_major_main_carries(version: str, limit: int) -> None:
    assert limit_of(version) == limit


#: One deprecated line per kind the walker writes, each dated 12: the rules read every kind.
KINDS = [
    Line("cli untaped awx put", DeprecatedMark(12, "untaped awx set")),
    Line("cli untaped jira me -> untaped jira whoami", DeprecatedMark(12)),
    Line("cli untaped jira logs -F -> --follow", DeprecatedMark(12)),
    Line("cli untaped awx test run --dry-run switch", DeprecatedMark(12, "--limit 1 --yes")),
    Line("cfg acme.old_mode boolean default=false", DeprecatedMark(12, "acme.mode")),
    Line(
        "cfg ansible.stale_after_seconds -> ansible.caches.index.max_age_seconds",
        DeprecatedMark(12),
    ),
    Line("sdk untaped.sdk.old(x)", DeprecatedMark(12, "untaped.sdk.new")),
    Line("sdk untaped.sdk.echo(err=) default=false", DeprecatedMark(12, "Diagnostics")),
    Line("contract acme.Source.items()", DeprecatedMark(12)),
    Line("behaviour untaped.plaintext-token", DeprecatedMark(12, "untaped auth migrate")),
]


@pytest.mark.parametrize("line", KINDS, ids=lambda line: line.identity)
def test_every_kind_is_overdue_at_its_major(line: Line) -> None:
    assert overdue([line], 11) == []
    assert overdue([line], 12) == [line]
    assert overdue([line], 13) == [line]


def test_overdue_skips_undated_experimental_and_stable_lines() -> None:
    lines = [
        Line("cli untaped a", DeprecatedMark()),
        Line("cli untaped b", "experimental"),
        Line("cli untaped c"),
        Line("cli untaped d", DeprecatedMark(11)),
    ]
    assert overdue(lines, 12) == [lines[3]]


def test_an_undated_line_fails_unless_allowed() -> None:
    lines = [
        Line("sdk untaped.sdk.report_error(exc)", DeprecatedMark()),
        Line("sdk untaped.sdk.other(x)", DeprecatedMark(None, "y")),
        Line("sdk untaped.sdk.dated(x)", DeprecatedMark(12)),
    ]
    assert undated(lines) == [lines[1]]


def _promises(baseline: list[Line], current: list[Line], version: str = "11.3.0") -> list[str]:
    return broken_promises(baseline, current, tag="v11.2.0", limit=limit_of(version))


def test_a_shown_promise_never_shortens() -> None:
    (message,) = _promises(
        [Line("cli untaped awx put", DeprecatedMark(13))],
        [Line("cli untaped awx put", DeprecatedMark(12))],
    )
    assert message.endswith("v11.2.0 promised 13.0; a shown promise never shortens")


def test_a_postponement_keeps_the_promise() -> None:
    assert not _promises(
        [Line("cli untaped awx put", DeprecatedMark(12))],
        [Line("cli untaped awx put", DeprecatedMark(13))],
    )


def test_shortening_before_any_release_showed_the_date_passes() -> None:
    assert not _promises(
        [Line("cli untaped awx put", DeprecatedMark())],
        [Line("cli untaped awx put", DeprecatedMark(12))],
    )
    assert not _promises([], [Line("cli untaped awx put", DeprecatedMark(12))])


def test_dropping_a_shown_mark_fails() -> None:
    (message,) = _promises(
        [Line("cfg acme.old -> acme.new", DeprecatedMark(12))],
        [Line("cfg acme.old -> acme.new")],
    )
    assert "v11.2.0 promised users this goes in 12.0; keep the mark or remove the thing" in message


def test_removing_a_dated_line_is_not_this_checks_business() -> None:
    # test_surface's removal_exempt decides whether it may go.
    assert not _promises([Line("cli untaped awx put", DeprecatedMark(12))], [])


def test_a_major_release_keeps_what_no_release_warned_about() -> None:
    line = Line("cli untaped awx put", DeprecatedMark(12))
    baseline = [Line("cli untaped awx put")]

    # In 11.x nothing is being released, so rule 1 alone speaks (removed_in=12 is fine there).
    assert not _promises(baseline, [line], version="11.3.0")
    (message,) = _promises(baseline, [line], version="12.0.0rc1")
    assert message.endswith(
        "deprecated since v11.2.0, the last release, so no release has warned about it; "
        "a deprecation ships in a minor first; set removed_in=13"
    )
    # Rule 1 leaves that line to rule 3, so one message speaks.
    assert overdue([line], 12, unwarned=unwarned_identities(baseline, [line])) == []


def test_the_rc_prep_final_sequence() -> None:
    released = [Line("cli untaped awx put", DeprecatedMark(12))]
    # After the 12.0.0rc1 release PR, main says 12.0.0rc1: the leftover is overdue.
    assert overdue(released, limit_of("12.0.0rc1")) == released
    # A prep PR removes it; the final release PR finds nothing left.
    assert overdue([], limit_of("12.0.0")) == []
    assert not _promises(released, [], version="12.0.0")
