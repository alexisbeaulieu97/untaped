"""The ``system`` a suite failure or error names: which part of a run is at fault.

:mod:`untaped_awx.domain.suites.case_failure` says when each is blamed.
"""

from __future__ import annotations

from untaped.sdk import Service
from untaped_awx.settings import AwxSettings

AWX = Service(AwxSettings)
"""AWX itself: the system an :class:`~untaped_awx.errors.AwxError` names until one of
the parts below is blamed."""

SUITE = Service(AwxSettings, part="suite")
CREDENTIALS = Service(AwxSettings, part="credentials")
CONTROLLER = Service(AwxSettings, part="controller")
SCM = Service(AwxSettings, part="scm")
INVENTORIES = Service(AwxSettings, part="inventories")
HOSTS = Service(AwxSettings, part="hosts")
PLAYBOOK = Service(AwxSettings, part="playbook")
EXPECTATION = Service(AwxSettings, part="expectation")
