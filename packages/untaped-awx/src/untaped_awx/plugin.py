"""The ``awx`` plugin class: what the plugin has, each under its own name.

The ``untaped.plugins`` entry point names :class:`Awx`, in this leaf module:
the class imports every command, so the records, the settings and the lint
providers import without them.
"""

from __future__ import annotations

from untaped.sdk import OnlineCheck, Plugin, StubSkill
from untaped_awx.commands.controller import Ping, Schema, probe
from untaped_awx.commands.documents import Apply, Export
from untaped_awx.commands.jobs import JobsGroup
from untaped_awx.commands.lint import Lint
from untaped_awx.commands.resources import resource_groups
from untaped_awx.commands.test import TestGroup
from untaped_awx.commands.unified_templates import UnifiedTemplatesGroup
from untaped_awx.domain.lint.resources import Resources
from untaped_awx.infrastructure.resources.specs import ALL_SPECS
from untaped_awx.providers.lint import AwxDocuments, AwxRules
from untaped_awx.settings import RESOURCES, TOKEN, AwxSettings


class Awx(Plugin):
    """Talk to Ansible Automation Platform / AWX."""

    settings = AwxSettings
    commands = (
        Ping,
        Schema,
        Apply,
        Export,
        Lint,
        JobsGroup,
        UnifiedTemplatesGroup,
        TestGroup,
        *resource_groups(),
    )
    fills = (AwxRules, AwxDocuments)
    records = (Resources, *(spec.require_record() for spec in ALL_SPECS))
    resources = RESOURCES
    token = TOKEN
    api = OnlineCheck(probe=probe)
    skill = StubSkill(
        "Ansible Automation Platform (AAP) and AWX resources and jobs: job and workflow "
        "templates, inventories, projects and schedules, launching, syncing and following "
        "jobs, export and apply documents, linting them, and `awx test` suites that prove a "
        "playbook, role or template change. Use when the work concerns AAP, AWX, Tower or an "
        "automation controller."
    )


__all__ = ["Awx"]
