"""Doctor checks the ansible capability contributes to root ``untaped doctor``.

Checks run offline.
"""

from __future__ import annotations

from untaped.capability_api import executable_check

DOCTOR_CHECKS = (executable_check("ansible.git", "git", purpose="git-backed source refresh"),)
