from __future__ import annotations

import pytest

from untaped_ansible.models.job_template import JobTemplate
from untaped_ansible.models.workflow_job_template import WorkflowJobTemplate


def test_job_template_requires_credentials() -> None:
    with pytest.raises(ValueError):
        JobTemplate(
            name="example",
            inventory="Inventory",
            project="Project",
            playbook="site.yml",
            credentials=[],
        )


def test_workflow_job_template_requires_nodes() -> None:
    with pytest.raises(ValueError):
        WorkflowJobTemplate(name="workflow", workflow_nodes=[])
