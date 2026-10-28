"""``untaped awx lint``: the awx rules, and any other plugin's, over AWX resources.

It builds one ``awx.resources`` subject (apply documents, offline or
resolved on the controller, or the controller's own records, kept in
``awx.caches.resources``) and hands it to lint's engine. This module is the
command's options and its usage checks; the loaders and lint's engine load
when it runs (:mod:`untaped_awx.commands._lint_runner`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from untaped.sdk import (
    Document,
    KeyValue,
    Matched,
    Output,
    Positional,
    Refresh,
    UsageError,
    experimental,
)
from untaped_awx.commands._awx import AwxCommand
from untaped_awx.commands._options import reject_duplicate_org_scope
from untaped_awx.document import Document as AwxDocument
from untaped_awx.settings import RESOURCES
from untaped_lint.api import Finding, Findings


class Linting(BaseModel):
    """Read when linting AWX documents or the controller, reading an awx finding or its
    fix, narrowing what is reported, or writing a rule over AWX resources.

    `untaped awx lint` checks AWX resources against rules before `apply` writes
    them or after the controller holds them. It runs on lint's engine, so the
    ids, `--ignore`, `--select`, `--strict` and the exit codes are `untaped
    lint`'s (`untaped docs lint guides findings`). It never writes to the controller.

    ## Three origins

    - `untaped awx lint ./specs --offline` reads apply documents only: no profile, no token;
      references resolve in the batch; a kind with no document in it is not checked.
    - `untaped awx lint ./specs` reads apply documents, then the controller; references resolve in
      the batch first, then on the controller.
    - `untaped awx lint` reads every resource the token can read; references resolve by id, in what
      the controller holds.

    FILES are files or directories, read as `apply` reads them (`*.yml` and
    `*.yaml` under a directory). Documents are linted as written:
    `awx.default_organization` is not applied, so the same files give the same
    results in every profile.

    Without FILES the controller is read once into a snapshot kept for an hour
    (`awx.caches.resources.max_age_seconds`), one per controller and token, so
    profiles that share both share it; `--refresh` reads it again now,
    `--cached` reads only what is kept. The snapshot redacts secret-named
    launch and inventory variables before it is kept.

    On the controller every reference resolves by the id AWX holds (a foreign
    key, a schedule's parent, what a workflow node runs and prompts with), so a
    project in another organization, or a credential or template in none, is
    found and never confused with a namesake. A kind the token cannot list is
    skipped with a warning and
    references to it are `unresolved`. A record no listed row holds is named
    from its referrer's `summary_fields` where AWX embeds it, else `#<id>` when
    the token cannot read it either (`project '#10' was not checked`); it is
    asked for once at most.

    ## Reading a finding

    Each row is a `lint.finding`: `rule` (`awx.missing-reference`),
    `severity`, `target`, `message`, `location`, `hint` and, on the controller,
    `fix`.

    - `target` is `Kind/Organization/name`, the organization being the
      resource's own or its parent's (a schedule's template's, a host's
      inventory's), `-` when it has none: `JobTemplate/Platform/deploy-web`,
      `Organization/-/Default`. An unreadable document's target is its path.
    - `location` is `{path, index}` for a document (`index` counts the file's
      `---` documents from 1, empty ones included) and `{id}` for the
      controller (`job_templates/14`).
    - `fix`, when set, is the argv after `untaped` that fixes it:
      `["awx", "job-templates", "patch", "deploy-web", "--organization",
      "Platform", "--set", "ask_limit_on_launch=true"]`; a resource in no
      organization is patched by id (`patch 102 --by-id`), since its name alone
      would select any organization's namesake too.

    ## Rules

    - `awx.missing-reference` (error, origins: both) fires when a reference names nothing in its
      origin: not in the batch (offline), not in the batch or on the controller (online), not on the
      controller (live); a template's `project`, or its `inventory` beside `ask_inventory_on_launch:
      false`, written as `null`; a workflow node whose template is gone.
    - `awx.unresolved-reference` (warning, origins: both) fires when a reference that was not
      checked: offline, to a kind the batch has no document of, or an id; online, to a kind the
      token cannot list or a record it cannot read.
    - `awx.ignored-launch-field` (error, origins: both) fires when a schedule's launch field or a
      workflow node's prompt is set while its template's `ask_<field>_on_launch` is false (extra
      variables: those its enabled survey does not ask for); a field the template cannot prompt for
      at all.
    - `awx.branch-override-ignored` (error, origins: both) fires when a job template sets
      `scm_branch` and its project's `allow_override` is false, so jobs run the project's branch.
    - `awx.survey-default-missing` (warning, origins: both) fires when a schedule or workflow node
      launches a template whose enabled survey has a required question with no default that the
      schedule's `extra_data` (a node's `prompts.extra_vars`, or its workflow's variables) does not
      answer.
    - `awx.unknown-field` (warning, origins: documents) fires when a spec field untaped knows no
      metadata for (often a typo): `apply` would send it as it is.
    - `awx.playbook-missing` (error, origins: controller) fires when a job template's playbook is
      not one its project found at its last successful sync.
    - `awx.empty-inventory` (warning, origins: controller) fires when an inventory has no hosts.
    - `awx.plaintext-secret` (error, origins: documents) fires when a secret path (a survey's
      password default, a credential's secret-named input such as `inputs.password`) or a
      secret-named variable (`db_password` in launch variables, `ansible_password` in an
      inventory's, group's or host's) holds a plain value; `$encrypted$`, an empty value or a `{{ …
      }}` expression is fine.
    - `awx.invalid-document` (error, origins: documents) fires when a document is not YAML, not a
      mapping, not a valid resource, or of a kind untaped does not know.

    A rule judges only what a document writes: a flag or field left out is a
    partial update, not a value. A rule whose template or project is missing
    says nothing; `awx.missing-reference` already does.

    `untaped lint rules` lists every rule installed, other plugins' included.

    ## Narrowing what is reported

    Selection narrows what is reported, never what is read: a template's
    finding about a project still needs the project read.

    - `--kind job-templates` (a CLI name or `JobTemplate`) reports that kind
      only; `--organization Platform` only resources in it. Both are target
      globs (`JobTemplate/Platform/**`). A document that could not be read is
      reported whatever they say.
    - `--filter KEY=VALUE` (repeatable) and `--search TEXT` ask the controller
      which resources to report, one listing per kind (`--kind`'s, else every
      kind with the filtered fields), as `export` does, and exactly those
      resources are reported (by location: two schedules of one name under two
      templates share a target, never a location). They need the live
      origin: with FILES or `--cached`, or with a field no such kind has, they
      are a usage error. A query that matches nothing prints a warning and
      exits 0.
    - `--ignore RULE[:GLOB]` leaves a rule out (where the target matches the
      glob); `--select RULE` reports only the rules named. The profile's
      `lint.ignore` and the repository's `.untaped/lint.yml` `ignore:` apply
      too, when run inside that repository.
    - `--strict` fails on warnings too.

    ## In a repository

    A repository of apply documents lints them with `untaped lint`, offline, by
    naming awx as a reader in `.untaped/lint.yml`:

    ```yaml
    version: 1
    name: house
    readers:
      awx: ["specs/**/*.yml"]
    ```

    The files the globs match are read as `awx lint FILES --offline` reads them,
    as one batch. A file that is not UTF-8 is lint's `lint.unreadable-file`.

    ## Writing a rule over AWX resources

    A rule reads the `awx.resources` subject through `untaped_awx.api` and
    reports with `untaped_lint.api`, in a repository's `.untaped/lint/` or a
    plugin's `Rules`:

    ```python
    from collections.abc import Iterator

    from untaped_awx.api import Resources, JobTemplateDoc, location_of, target_of
    from untaped_lint.api import Finding, finding, rule


    @rule("has-credential", reads=Resources, severity="warning",
          summary="Every job template carries a credential.")
    def has_credential(resources: Resources) -> Iterator[Finding]:
        for item in resources.of("JobTemplate"):
            if not JobTemplateDoc.model_validate(item.resource.spec).credentials:
                yield finding(has_credential, target_of(item), "no credential",
                              location=location_of(item))
    ```

    Each item is a `Linted`: the apply document (`resource`), where it was read
    (`origin`, `location`), its `references` (each `resolved`, `absent` or
    `unresolved`) and, from the controller, `controller` facts (`status`,
    `total_hosts`, `playbooks`). `resources.find(reference)` gives the item a
    `resolved` reference names, else `None`. A live reference carries the
    `location` of the item it was resolved to by id, and is found there; a
    document's is found by its target, `None` when no item or several answer
    (a rule never judges an arbitrary namesake). Test a rule with
    `untaped_awx.testing.resources(yaml_text)` and `untaped_lint.testing.run_rule`.
    """

    offline: bool = Field(
        False, description="Resolve references among FILES only: no profile, no controller."
    )
    kind: str | None = Field(
        None,
        description="Report only this kind: a CLI name (job-templates) or a kind (JobTemplate).",
    )
    organization: str | None = Field(None, description="Scope to organization.")
    filter_: list[KeyValue] = Field(
        default_factory=list,
        description="Server-side filter KEY=VALUE (repeatable).",
    )
    search: str | None = Field(None, description="Server-side search.")


class Lint(AwxCommand):
    """Lint AWX resources: apply documents, or what the controller holds."""

    stability = experimental

    files: Positional[list[Annotated[Path, Document(AwxDocument)]]] = Field(
        default_factory=list,
        description=(
            "Apply documents: files, or directories read as apply reads them. "
            "Without them, the controller's resources are linted."
        ),
    )
    linting: Linting
    refresh: Refresh = Refresh(RESOURCES)
    findings: Findings = Field(default_factory=Findings)
    output: Output[Finding]

    def run(self) -> Iterator[Finding]:
        from untaped_awx.commands._lint_runner import (  # noqa: PLC0415 - lint's engine
            Query,
            run_lint,
        )

        self.findings.check()
        linting = self.linting
        filters = dict(linting.filter_)
        queried = bool(filters) or linting.search is not None
        refresh = self.refresh.directive
        if linting.offline and not self.files:
            raise UsageError("--offline reads FILES only; pass the documents to lint")
        if self.files and refresh is not None:
            raise UsageError("--refresh and --cached apply to the controller snapshot, not FILES")
        if queried and (self.files or refresh is False):
            raise UsageError(
                "--filter and --search ask the controller, which FILES and --cached do not; "
                "narrow with --kind or --organization"
            )
        reject_duplicate_org_scope(linting.organization, filters)
        query = Query(
            kind=linting.kind,
            organization=linting.organization,
            filters=filters,
            search=linting.search,
        )
        report = run_lint(
            self.files,
            offline=linting.offline,
            refresh=refresh,
            query=query,
            findings=self.findings,
            awx=self.awx,
        )
        if report is None:
            return
        diag = self.ctx.diag
        for warning in report.warnings:
            diag.warning(warning)
        for failure in report.failures:
            diag.failure(failure)
        yield from report.findings
        diag.info(report.summary())
        if report.predicate_hit(strict=self.findings.strict):
            raise Matched


__all__ = ["Lint", "Linting"]
