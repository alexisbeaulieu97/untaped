"""Lint the repository's example plugins and run the deprecated ``plugin check`` on one: a script.

``test_examples.py`` runs it in its own process: the examples declare record
kinds core's test fakes declare too, so they can't share a test process.
Prints one JSON object: per example the ``(rule, severity)`` of each finding
and the failure count, and ``untaped plugin check shelf --format json``'s
exit code, rows and warnings.
"""

from __future__ import annotations

import json
import sys
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Any

EXAMPLES = Path(__file__).resolve().parents[5] / "examples"
NAMES = ("hello", "shelf", "library")


def _project(name: str) -> dict[str, Any]:
    path = EXAMPLES / f"untaped-{name}" / "pyproject.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]


def _target(name: str) -> str:
    """The example's entry point, read from its pyproject.toml."""
    return str(_project(name)["entry-points"]["untaped.plugins"][name])


def _requires(name: str) -> tuple[str, ...]:
    """The example's ``Requires-Dist``, read from its pyproject.toml."""
    project = _project(name)
    extras = project.get("optional-dependencies", {})
    return (
        *project["dependencies"],
        *(f'{line}; extra == "{extra}"' for extra, lines in extras.items() for line in lines),
    )


def main() -> None:
    for name in NAMES:
        sys.path.insert(0, str(EXAMPLES / f"untaped-{name}" / "src"))
    from untaped.runtime.bootstrap import build_root_app
    from untaped.sdk import installed_plugins
    from untaped.sdk.testing import CliInvoker, PluginCandidate, compose_with
    from untaped_lint.application.subjects import plugin_subject
    from untaped_lint.testing import run

    candidates = [
        PluginCandidate(
            distribution=f"untaped-{name}",
            name=name,
            target=_target(name),
            requires_dist=_requires(name),
        )
        for name in NAMES
    ]
    candidates.append(
        PluginCandidate(
            distribution="untaped-lint",
            name="lint",
            target="untaped_lint:SPEC",
            distribution_version=metadata.version("untaped-lint"),
        )
    )
    out: dict[str, object] = {}
    for name in NAMES:
        with compose_with(*candidates):
            plugin = next(each for each in installed_plugins() if each.name == name)
            report = run([plugin_subject(plugin)], candidates=candidates)
        out[name] = {
            "findings": sorted([each.rule, each.severity] for each in report.findings),
            "failures": [each.message for each in report.failures],
        }
    result = CliInvoker().invoke(
        build_root_app(candidates=candidates).meta, ["plugin", "check", "shelf", "-f", "json"]
    )
    out["plugin check shelf"] = {
        "exit_code": result.exit_code,
        "rows": json.loads(result.stdout),
        "warnings": [line for line in result.stderr.splitlines() if line.startswith("warning:")],
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
