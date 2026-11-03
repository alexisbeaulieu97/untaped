"""awx fills ansible's ``CheckoutSource`` with its job templates.

``untaped ansible impact checkouts ROLE`` asks it which job templates check
out each dependent of ROLE. :meth:`AwxTemplates.checkouts` lists the job
templates live (the contract's answer cache keeps the last answer) and
reads each project once for its repository and the ref a job checks out.
Only Git projects check a repository out: manual (local-path) projects,
templates without a readable project and other SCM types are left out and
counted in one warning. Workflow templates have no project of their own.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from untaped.sdk import UsageError, key, plural, q
from untaped.sdk.contracts import NotReady, Provider
from untaped_ansible.api import Checkout, CheckoutSource
from untaped_awx.application.resources.template_scm import with_scm
from untaped_awx.infrastructure.resources.catalog import AwxResourceCatalog
from untaped_awx.records import JobTemplate
from untaped_awx.server import SERVER_API
from untaped_awx.settings import AwxSettings


class AwxTemplates(CheckoutSource[JobTemplate], Provider[AwxSettings]):
    """The job templates of the profile's controller, as the repositories they check out."""

    def ready(self) -> NotReady | None:
        unready = super().ready()
        if unready is None and self.settings.server is None:
            return NotReady("no controller is set", key(AwxSettings).server.url)
        return unready

    def to_checkout(self, item: JobTemplate) -> Checkout:
        if not item.scm_url:
            raise UsageError(
                f"job template {q(item.name)} names no repository",
                hint="list job templates with --with-scm, from a Git project",
            )
        return Checkout(
            name=item.name,
            url=item.scm_url,
            ref=item.effective_scm_ref or None,  # AWX's "" is the repository's default branch
            via=_via(item),
        )

    def checkouts(self) -> list[Checkout]:
        api = SERVER_API.port(self.settings)
        catalog = AwxResourceCatalog()
        records = list(api.list(catalog.get("JobTemplate")))
        rows = with_scm(
            records,
            client=api,
            catalog=catalog,
            warn=self.ctx.diag.warning,
            project_fields=("scm_type",),
        )
        found: list[Checkout] = []
        skipped: Counter[str] = Counter()
        for row in rows:
            why = _not_checked_out(row)
            if why is None:
                try:
                    found.append(self.to_checkout(JobTemplate.model_validate(row)))
                    continue
                except ValidationError, UsageError:
                    why = "with a repository URL untaped can't read"
            skipped[why] += 1
        if skipped:
            reasons = ", ".join(f"{count} {why}" for why, count in sorted(skipped.items()))
            self.ctx.diag.warning(
                f"awx: {plural(skipped.total(), 'job template')} left out ({reasons})"
            )
        return found


def _not_checked_out(row: dict[str, Any]) -> str | None:
    """Why the job template ``row`` checks out no Git repository, or ``None`` when it does."""
    scm_type = row.get("project_scm_type")
    if scm_type is None:
        return "without a readable project"
    if scm_type == "":
        return "on a local-path project"
    if scm_type != "git":
        return f"on a project of type {scm_type}"
    return None


def _via(item: JobTemplate) -> str | None:
    """``project NAME`` from the template's summary fields, when AWX sent them."""
    summary = (item.model_extra or {}).get("summary_fields")
    project = summary.get("project") if isinstance(summary, dict) else None
    name = project.get("name") if isinstance(project, dict) else None
    return f"project {name}" if isinstance(name, str) and name else None


__all__ = ["AwxTemplates"]
