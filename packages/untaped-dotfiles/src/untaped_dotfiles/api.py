"""What another plugin may import from dotfiles: the ``StepKind`` contract and its records.

A plugin adds a step kind by filling :class:`StepKind` with its own record of
``with:`` (``StepKind[Defaults]``) and listing its provider in its plugin
class's ``fills``; a package then writes ``kind: <plugin>.defaults``.
"""

from __future__ import annotations

from untaped_dotfiles.step_kinds import StepFields, StepKind, StepResult

__all__ = ["StepFields", "StepKind", "StepResult"]
