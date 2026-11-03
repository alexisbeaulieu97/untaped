"""awx fills ansible's ``CheckoutSource`` with its job templates (design S3 and S7).

Each Git project's job templates are checked out at their effective ref;
templates on a local-path or non-Git project, or with no readable project,
are left out and counted in one warning.
"""

from __future__ import annotations

from typing import Any

import pytest

from awx_test_support import installed
from untaped.sdk import Message, UsageError
from untaped.sdk.contracts import Ok, gather
from untaped.sdk.testing import assert_fills, piped_through
from untaped_ansible.api import Checkout, CheckoutSource
from untaped_awx.providers.ansible import AwxTemplates
from untaped_awx.records import JobTemplate

URL = "https://github.com/acme/web.git"


def _seed(fake: Any) -> None:
    fake.seed("organizations", id=1, name="Default")
    fake.seed(
        "projects", id=10, name="web", organization=1, scm_type="git", scm_url=URL,
        scm_branch="main", allow_override=True,
    )  # fmt: skip
    fake.seed(
        "projects", id=11, name="default", organization=1, scm_type="git", scm_url=URL,
        scm_branch="", allow_override=False,
    )  # fmt: skip
    fake.seed("projects", id=12, name="local", organization=1, scm_type="", scm_url="")
    fake.seed("projects", id=14, name="path", organization=1, scm_type="git", scm_url="/srv/web")
    fake.seed(
        "projects", id=13, name="old", organization=1, scm_type="svn",
        scm_url="https://svn.example.com/web",
    )  # fmt: skip
    for id_, name, project, branch in [
        (42, "Deploy web", 10, ""),
        (43, "Release web", 10, "release-2.1"),
        (44, "Nightly web", 11, ""),
        (45, "Local", 12, ""),
        (46, "Legacy", 13, ""),
        (47, "Orphan", None, ""),
        (48, "On disk", 14, ""),
    ]:
        fake.seed(
            "job_templates",
            id=id_,
            name=name,
            organization=1,
            project=project,
            scm_branch=branch,
            summary_fields={"project": {"id": project, "name": f"acme/{project}"}},
        )


def _checkouts() -> tuple[list[Checkout], list[str]]:
    """awx's answer, and the warnings it said while listing."""
    with installed().running() as current:
        answers = gather(CheckoutSource.checkouts, refresh=True)()
        [answer] = list(answers)
        said = current.diagnostics
    assert isinstance(answer, Ok), answer
    warnings = [d.message for d in said if isinstance(d, Message) and d.level == "warning"]
    return answer.value, warnings


def test_s3_each_git_job_template_checks_out_its_project_at_its_effective_ref(
    fake_aap: Any,
) -> None:
    _seed(fake_aap)

    found, _ = _checkouts()

    assert [(c.name, str(c.url), c.ref, c.via) for c in found] == [
        ("Deploy web", URL, "main", "project acme/10"),
        ("Release web", URL, "release-2.1", "project acme/10"),
        ("Nightly web", URL, None, "project acme/11"),
    ]
    deploy = found[0]
    assert deploy.source is not None
    assert (deploy.source.plugin, deploy.source.kind) == ("awx", "awx.job_template")
    assert deploy.source.record["id"] == 42


def test_s7_templates_that_check_out_no_git_repository_are_counted_once(
    fake_aap: Any,
) -> None:
    _seed(fake_aap)

    _, warnings = _checkouts()

    assert warnings == [
        "awx: 4 job templates left out (1 on a local-path project, 1 on a project of type svn, "
        "1 with a repository URL untaped can't read, 1 without a readable project)"
    ]


def test_a_piped_job_template_becomes_a_checkout() -> None:
    template = JobTemplate(
        id=42, name="Deploy web", scm_url=URL, effective_scm_ref="", project_allow_override=True
    )

    checkout = piped_through(CheckoutSource.to_checkout, template)

    assert (checkout.name, checkout.ref) == ("Deploy web", None)
    with pytest.raises(UsageError, match="names no repository"):
        piped_through(CheckoutSource.to_checkout, JobTemplate(id=1, name="Bare"))


def test_awx_fills_checkout_source() -> None:
    assert_fills(AwxTemplates, samples=[JobTemplate(id=1, name="Deploy web", scm_url=URL)])
