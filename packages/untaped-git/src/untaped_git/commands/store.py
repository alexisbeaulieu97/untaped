"""``untaped git store``: what the repo store holds and costs, read from disk alone."""

from __future__ import annotations

from collections.abc import Callable

from untaped.sdk import Command, Output, plural, shown_path, size_text
from untaped_git.records import RepoCount, StoreReport
from untaped_git.report import store_report
from untaped_git.settings import GitSettings, store_root
from untaped_git.version import git_version, version_text


def _lines(report: StoreReport) -> list[str]:
    return list(report_lines(report))


class Store(Command[GitSettings]):
    """Report what the repo store holds and costs, read from disk alone.

    Shows the repos, their size, which plugins use them and the size of the
    repos only one plugin uses (what releasing them would free), repos an
    interrupted release left (finished on their next use) or that only a
    branch or stash keeps, pack counts, hosts that ignore the partial-clone
    filter, and repos where git paused automatic maintenance. The table is
    one labelled line each; `--columns` prints it as a table.

    Each plugin's own delete or remove command releases the repos it uses;
    a repo stays while another plugin, a hand-added worktree, a branch or a
    stash holds it, and untaped never deletes those branches or stashes.
    Never edit a repo under the store by hand: other plugins' refs and
    worktrees share it. Their refs live under `refs/untaped/` and show in
    `git log --all` from an untaped worktree.

    The store needs Git 2.29 or newer; `untaped doctor` checks the version
    and the store.
    """

    output: Output[StoreReport] = Output(render={"table": _lines})

    def run(self) -> StoreReport:
        version = git_version()
        shown = None if version is None else version_text(version)
        return store_report(store_root(), version=shown)


def report_lines(report: StoreReport) -> list[str]:
    """The report as ``untaped git store`` prints it in table format: one labelled line each.

    A line shows only when it has something to say.
    """
    rows = [("store", _store_line(report))]
    if report.used_by:
        rows.append(("used by", _joined(report.used_by, lambda count: f"{count:,}")))
    if report.exclusive_bytes:
        rows.append(("exclusive", _joined(report.exclusive_bytes, size_text)))
    if report.unowned.repos:
        rows.append(
            (
                "unowned",
                f"{_repos_text(report.unowned)} (interrupted release; finished on next use)",
            )
        )
    if report.held_by_branches.repos:
        rows.append(
            (
                "held by branches",
                f"{_repos_text(report.held_by_branches)} "
                "(only a branch or stash a release left keeps them)",
            )
        )
    if report.repos:
        rows.append(
            (
                "packs",
                f"median {report.packs_median:g} · max {report.packs_max} · "
                f"loose objects {report.loose_objects:,}",
            )
        )
    for host, count in report.filter_ignored.items():
        rows.append(("filter", f"ignored on {host}: {plural(count, 'repo')} hold every blob"))
    if report.gc_log:
        rows.append(("gc.log", f"{plural(len(report.gc_log), 'repo')} (auto maintenance paused)"))
    width = max(len(label) for label, _ in rows)
    return [f"{label:<{width}}  {text}" for label, text in rows]


def _store_line(report: StoreReport) -> str:
    parts = [
        shown_path(report.store_dir),
        f"{report.repos:,} {'repo' if report.repos == 1 else 'repos'}",
        size_text(report.size_bytes),
    ]
    if report.git_version:
        parts.append(f"git {report.git_version}")
    return "    ".join(parts)


def _repos_text(count: RepoCount) -> str:
    return f"{plural(count.repos, 'repo')}, {size_text(count.size_bytes)}"


def _joined(values: dict[str, int], shown: Callable[[int], str]) -> str:
    return " · ".join(f"{name} {shown(value)}" for name, value in values.items())
