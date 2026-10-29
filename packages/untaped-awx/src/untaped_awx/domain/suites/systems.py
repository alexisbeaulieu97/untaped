"""The ``system`` a suite failure or error names: which part of a run is at fault.

:mod:`untaped_awx.domain.suites.case_failure` says when each is blamed.
"""

from __future__ import annotations

SUITE = "awx.suite"
CREDENTIALS = "awx.credentials"
CONTROLLER = "awx.controller"
SCM = "awx.scm"
INVENTORIES = "awx.inventories"
HOSTS = "awx.hosts"
PLAYBOOK = "awx.playbook"
EXPECTATION = "awx.expectation"
