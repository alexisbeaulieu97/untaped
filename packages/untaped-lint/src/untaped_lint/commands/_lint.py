"""What every lint command shares: the lint file it reads."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field

from untaped.sdk import Command, Document
from untaped_lint.application.repo_file import LintFile
from untaped_lint.config import LintConfig, lint_config
from untaped_lint.settings import LintSettings


class LintCommand(Command[LintSettings]):
    """Base of lint's commands: the lint file they read, found or named by ``--config``."""

    config: Annotated[Path | None, Document(LintFile)] = Field(
        None,
        description="Read this lint file instead of .untaped/lint.yml, and lint the current "
        "directory.",
    )

    def lint_config(self, *, tree: bool = True) -> LintConfig:
        """The profile's ``lint.ignore`` and the lint file found from the working directory."""
        return lint_config(Path.cwd(), config=self.config, tree=tree)
