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
from importlib import import_module, metadata
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[5] / "examples"
NAMES = ("hello", "shelf", "library")
#: Each example's ``Requires-Dist``, as its pyproject.toml declares it.
REQUIRES = {
    "hello": ("untaped>=11,<12",),
    "shelf": ("untaped>=11,<12",),
    "library": ("untaped>=11,<12", 'untaped-shelf>=0.1,<0.2; extra == "shelf"'),
}


def main() -> None:
    for name in NAMES:
        sys.path.insert(0, str(EXAMPLES / f"untaped-{name}" / "src"))
    from untaped.bootstrap import build_root_app
    from untaped.sdk import installed_plugins
    from untaped.testing import CliInvoker, PluginCandidate, compose_with, plugin_candidate
    from untaped_lint.application.subjects import plugin_subject
    from untaped_lint.testing import run

    candidates = [
        plugin_candidate(import_module(f"untaped_{name}").SPEC, requires_dist=REQUIRES[name])
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
