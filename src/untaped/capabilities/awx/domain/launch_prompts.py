"""Which template flag makes AWX accept each launch field.

AWX ignores a launch field unless its template prompts for it on launch
(``ask_<field>_on_launch``). The launch preflight reads these flags, and a
temporary test copy enables them for the fields its cases set.
"""

from __future__ import annotations

PROMPT_FLAGS: dict[str, str] = {
    "extra_vars": "ask_variables_on_launch",
    "limit": "ask_limit_on_launch",
    "inventory": "ask_inventory_on_launch",
    "credentials": "ask_credential_on_launch",
    "scm_branch": "ask_scm_branch_on_launch",
    "job_tags": "ask_tags_on_launch",
    "skip_tags": "ask_skip_tags_on_launch",
    "verbosity": "ask_verbosity_on_launch",
    "diff_mode": "ask_diff_mode_on_launch",
    "job_type": "ask_job_type_on_launch",
    "execution_environment": "ask_execution_environment_on_launch",
    "labels": "ask_labels_on_launch",
    "forks": "ask_forks_on_launch",
    "job_slice_count": "ask_job_slice_count_on_launch",
    "timeout": "ask_timeout_on_launch",
    "instance_groups": "ask_instance_groups_on_launch",
}
"""Launch field → the template flag that makes AWX accept it at launch."""

__all__ = ["PROMPT_FLAGS"]
