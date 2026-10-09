"""Doctor checks the ansible plugin contributes to root ``untaped doctor``.

Checks run offline.
"""

from __future__ import annotations

from untaped.sdk import executable_check

DOCTOR_CHECKS = (executable_check("ansible.git", "git", purpose="git-backed source refresh"),)
