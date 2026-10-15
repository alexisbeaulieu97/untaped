"""The root commands and ``untaped.management`` follow the conventions."""

from __future__ import annotations

from pathlib import Path

import untaped
import untaped.management
from untaped.bootstrap import SHELL_SPEC, build_root_app, composition
from untaped.conventions.help_tree import help_tree_violations
from untaped.conventions.messages import message_violations
from untaped.conventions.settings_names import settings_name_violations
from untaped.conventions.source import source_files
from untaped.conventions.stability import stability_violations
from untaped.plugins.registry import ROOT_MANAGEMENT_COMMANDS
from untaped.settings import Settings, model_sections


def _core_violations() -> list[str]:
    """Violations in the root commands, ``untaped.management`` and core's sections."""
    root = build_root_app(candidates=[])
    result = composition()
    management = Path(untaped.management.__file__).parent
    sections = {**model_sections(Settings), SHELL_SPEC.section: SHELL_SPEC.settings}
    src = Path(untaped.__file__).parent.parent
    return sorted(
        [
            *help_tree_violations(root, sorted(ROOT_MANAGEMENT_COMMANDS)),
            *stability_violations(root, result, ROOT_MANAGEMENT_COMMANDS, sections=sections),
            *message_violations(management, list(source_files(management))),
            *(
                line
                for section, model in sections.items()
                for line in settings_name_violations(section, model, src)
            ),
        ]
    )


def test_core_follows_the_conventions() -> None:
    assert _core_violations() == []
