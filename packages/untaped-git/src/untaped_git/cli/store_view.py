"""How ``untaped git store`` prints the store report in table format."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from untaped.sdk import plural, size_text
from untaped_git.domain.records import RepoCount, StoreReport


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
                f"{_repos_text(report.held_by_branches)} (a branch or stash a release kept; "
                "push it, or remove with --force, to reclaim it)",
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
        _home(report.store_dir),
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


def _home(path: str) -> str:
    home = str(Path.home())
    return f"~{path.removeprefix(home)}" if path == home or path.startswith(f"{home}/") else path
