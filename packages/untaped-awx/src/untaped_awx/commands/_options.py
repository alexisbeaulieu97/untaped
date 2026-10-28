"""The option bundles awx's commands share, and the rules those options follow.

A bundle's docstring is its guide: what to read before using its options.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, Field

from untaped.sdk import KeyValue, UsageError
from untaped_awx.commands.context import supported_scopes
from untaped_awx.domain import ResourceSpec

#: How a list of names reads in help.
NAMES = "Names of the resources to select, or AWX ids with --by-id."


class Selection(BaseModel):
    """Read before selecting AWX resources for a read or a write: selection modes,
    scopes and filters, how names resolve, and piping records between awx commands.

    ## Selection modes

    Selection modes are exclusive: names, names with `--by-id`, `--stdin`,
    `--filter`/`--search`, or `--all`. Commands that change things need an
    explicit selection or `--all`, which selects every resource in scope.

    ```console
    $ untaped awx inventory-sources list --inventory Production --inventory-organization Default
    $ untaped awx inventory-sources patch Cloud --inventory Production \\
        --inventory-organization Default --set update_cache_timeout=3600 --dry-run
    ```

    - Scope options (`--organization`, `--inventory`, `--inventory-organization`,
      `--parent`) constrain both the name lookup and filters. A group offers only
      the scopes its kind has; any other is a usage error (exit 2).
    - `--filter KEY=VALUE` passes AWX's lookup syntax through
      (`name__icontains=web`, `inventory__name=Production`).
    - A name that is not found names its scope and suggests close names. A
      second line says when `awx.default_organization` chose the organization;
      pass `--organization` to search elsewhere.
    - A name in several organizations is ambiguous: pass `--organization`, set
      `awx.default_organization`, or select by id.
    - The whole selection is resolved and validated before the first write; an
      empty or invalid selection writes nothing.
    - Tables name foreign keys; `json`, `yaml` and `pipe` carry complete records
      with ids unless `--columns` narrows them or `--with-names` names them.
    - Credentials, credential types, organizations and unified templates are
      read-only.

    ## Pipes between commands

    `--format pipe` emits typed records carrying their kind and id, and a
    `--stdin` consumer of the same kind uses the ids directly:

    ```bash
    untaped awx job-templates list --filter name__icontains=deploy --format pipe \\
      | untaped awx job-templates patch --stdin --set verbosity=2 --dry-run
    ```

    - Bare stdin lines are names (ids with `--by-id`); do not mix them with
      records.
    - A record of another kind (an `awx.host` piped into `projects patch`) exits
      2, and empty stdin is an error.
    - `--stdin` also accepts the kind's `awx.copy_outcome` and
      `awx.rename_outcome` records.
    - Confirmations read the terminal even when stdin is piped.

    ## What writes print

    Writes emit outcome rows (`awx.apply_outcome_row` for `apply`, `patch` and
    `edit`; `awx.delete_outcome`, `awx.copy_outcome`, `awx.rename_outcome`, …)
    whose `action` is `planned` in a preview. A failed row carries an `error`
    with `category`, `system`, `retryable`, `message` and `hint`; with
    `--format json`, stderr carries the same fields as JSON Lines.
    """

    by_id: bool = Field(
        False, description="Look up identifiers as AWX numeric ids instead of names."
    )
    search: str | None = Field(None, description="Server-side search.")
    filter_: list[KeyValue] = Field(
        default_factory=list,
        description=(
            "Server-side filter KEY=VALUE (repeatable), passed verbatim to AWX, so any "
            "Django-style lookup works: name__icontains=deploy."
        ),
    )
    all_: bool = Field(False, description="Explicitly select every resource in scope.")

    @property
    def filters(self) -> dict[str, str]:
        """``--filter`` as the query parameters AWX takes."""
        return dict(self.filter_)

    @property
    def queried(self) -> bool:
        """Whether a server-side query (``--filter`` or ``--search``) selects."""
        return bool(self.filter_) or self.search is not None


class Scope(BaseModel):
    """Base of the scope bundles: the options that narrow a kind's lookups.

    Each bundle says what it scopes in a comment: a bundle's own docstring
    would make it a guide.
    """

    def values(self) -> dict[str, str | None]:
        """Each scope option by its name, ``None`` where it was not given."""
        return {name: getattr(self, name) for name in type(self).model_fields}


# The organization a kind's names are looked up in.
class OrganizationScope(Scope):
    organization: str | None = Field(None, description="Scope to organization.")


# The parent resource a kind's names are looked up under.
class ParentScope(Scope):
    parent: str | None = Field(None, description="Scope to the parent resource name.")


# The inventory a host, group or inventory source is looked up in.
class InventoryScope(Scope):
    inventory: str | None = Field(
        None, description="Scope Host, Group, or InventorySource selection to an inventory."
    )
    inventory_organization: str | None = Field(
        None,
        description="Scope Host, Group, or InventorySource selection by inventory organization.",
    )
    parent: str | None = Field(None, description="Scope to the parent resource name.")


def scope_bundle(spec: ResourceSpec) -> type[Scope] | None:
    """The scope bundle of ``spec``'s commands, or ``None`` for a kind with no scope."""
    scopes = supported_scopes(spec)
    if "inventory" in scopes:
        return InventoryScope
    if "organization" in scopes:
        return OrganizationScope
    if "parent" in scopes:
        return ParentScope
    return None


def scope_values(scope: Scope | None) -> Mapping[str, str | None]:
    """A command's scope options by name; a kind with no scope has none."""
    return {} if scope is None else scope.values()


class Writes(BaseModel):
    """Read before changing anything on an AWX controller: how a write previews and
    confirms, how a batch runs and fails, what is verified, and what can't be undone.

    Every write (a command marked `(changes things)` or `(destructive)`)
    previews once and asks once, No by default.

    ## Preview and confirmation

    - `--dry-run` never writes, even with `--yes`; `apply --check` reports
      drift. The preview has one row per planned change (resource, field, old →
      new); `--yes` goes ahead without it.
    - Without a terminal, a write needs `--yes` or `--dry-run` (else exit 2).
    - A single named `launch` or `sync` submits at once; several targets, a
      query or a `--stdin` selection are listed and confirmed once.
    - Declining exits 1 with `cancelled; no changes made`.

    ## How a batch runs

    - Writes run one at a time unless `--parallel`. A failure fails its row and
      the rest still run, except a row that needs a resource whose create failed.
      `--fail-fast` stops scheduling new writes after the first failure, and a
      rejected token or broken configuration always does; writes already running
      finish and the rest are skipped.
    - There is no transaction or rollback; result rows keep the ids they reached.
    - Each write re-reads what it changes to detect a conflicting change.
      `delete` re-reads after an interactive prompt; with `--yes` the read just
      before the writes is the check.
    - A body write whose membership or survey write then fails is `partial`,
      keeping the resource id.
    - A write AWX does not read back as sent fails as unverified;
      `--allow-unverified` keeps it, still labelled unverified.

    ## Secrets

    Previews and results redact known secrets. Keep `$encrypted$` placeholders as
    they are: they preserve stored secrets. `awx.apply_outcome_row` rows list
    `fields_changed`, `preserved_secrets`, `dropped_undeclared_secrets`
    (`$encrypted$` placeholders at fields untaped doesn't treat as secrets, left
    out of the write) and `dropped_secrets` (secret placeholders a create left
    out, a new workflow node's `$encrypted$` extra vars included, so the new
    resource or node starts without them).

    ## Recovery

    There is no rollback, and a failed batch keeps what it wrote. An export taken
    before the change (`job-templates export Deploy --out deploy-before.yml`) is a
    recovery point: applying it restores the fields of a resource that still
    exists. A deleted resource comes back with a new id and without its secrets,
    access, schedules or history; a recreated template needs a new callback key
    if it used one. What a document can't carry:
    `untaped docs awx inputs document`.
    """

    fail_fast: bool = Field(
        False, description="Stop scheduling after the first runtime failure; the rest are skipped."
    )


class SourceRef(BaseModel):
    """Read before running awx test or apply with --source-ref: how specs at a git ref
    become temporary template copies, what such a run reports, cleaning up leftover
    copies, and applying files from a ref.

    `--source-ref REF` reads specs as they are at a git ref instead of the working
    tree. `awx test` uses it to run suites against temporary copies of the
    templates a change edits; `awx apply` uses it to apply files from a tag or
    branch. Either way, messages name the files read as `REF:PATH`.

    ## Test at a ref

    `--scm-branch` runs a branch's playbooks with the templates AWX holds. When
    the branch also changes a template or workflow, keep its spec (an apply
    document, as `export` writes it) in the repository and run `untaped awx test
    run --source-ref REF`: each suite runs against a temporary copy of its
    template as REF describes it.

    1. REF (a branch, tag or commit; `HEAD` once pushed) is pinned to its
       commit, which a remote must have. Suites and specs are read at that
       commit, never from the working tree.
    2. A suite binds to a spec by name: a `kind: JobTemplate` (for
       `jobTemplate`) or `kind: WorkflowJobTemplate` document anywhere under
       `.untaped/awx/` with the template's name and organization.
       - A copied workflow's nodes that run a template with a spec run that
         template's copy.
       - A spec in another organization does not bind, with a warning.
    3. Each copy is created as `apply` would create the spec, except:
       - its name is `NAME [untaped-test SHA RUN]` (7-digit commit and a random
         run id), so concurrent runs never collide;
       - its description carries the marker `untaped-test run=RUN ref=REF sha=SHA
         created=TIME` that `prune` finds leftovers by;
       - its `scm_branch` is the commit, so a job template's project must allow
         branch override; a workflow passes it to nodes that prompt for it;
       - it prompts on launch for every field a case sets in `launch`;
       - it has no webhook settings.

       Everything else a spec names must already exist: only the copies are
       created.
    4. Every template the run launches without copying it must prompt for
       `scm_branch`: a suite's template without a spec, and each template a
       workflow of the run runs without a copy. Otherwise it would run its own
       branch, so the run is refused before anything is created: add the
       template's spec under `.untaped/awx/` or enable
       `ask_scm_branch_on_launch`.
    5. The copies are created without a confirmation, the cases run, and the
       copies are deleted however the run ends (running jobs cancelled first);
       `--keep` keeps them.

    - A case's `launch.scm_branch` is replaced by the commit.
    - `--source-ref` cannot be combined with `--scm-branch` or `--baseline`
      (compare with `--compare`).
    - `--no-cancel` needs `--keep`: AWX cannot delete a template while its job
      runs.
    - `validate --source-ref REF` does everything but the writes. A case of a copied
      template is checked against the spec.
    - The AWX user needs to create and delete templates
      (`untaped docs awx commands test`).

    ## What a run with copies reports

    The run creates its copies before any case launches. A case of a copied
    template reports the copy's job, whose `scm_branch` is the commit. stderr
    names each suite that runs a template AWX holds (`deploy: runs AWX's
    JobTemplate 'Deploy' at 1a2b3c4 (no spec)`) and each copy created, with the
    prompts it enables.

    A copy that could not be provisioned is not a test result: the run stops
    before any launch with one error and prints no row.

    - The error starts `cannot provision the temporary test set of REF (nothing
      was created); nothing launched:` (or `(the copies created are torn
      down)`), or `cannot run REF; nothing launched:` when the run copies
      nothing.
    - It lists each problem as `LABEL: message`, and carries the worst problem's
      `category`, `system` and `hint`.
    - An invalid spec, or two specs matching one suite, fail alone as
      `awx.suite`, naming the files.

    - `awx.suite`, `not_found`, `conflict`, `invalid` (1): A spec names something AWX does not have,
      a copy's name is taken, or two specs match one suite. Fix the spec or suite (or create the
      missing resource in AWX), then `untaped awx test validate --source-ref REF`.
    - `awx.scm`, `invalid` (1): A copy's project does not allow branch override, or a template the
      run does not copy does not prompt for `scm_branch`. Enable `allow_override` on the project;
      add the template's spec or enable `ask_scm_branch_on_launch`.
    - `awx.credentials`, `auth`, `permission` (4): AWX refused to create a copy (401, 403). Give the
      AWX user the roles in `untaped docs awx commands test`.
    - `awx.controller`, `unavailable` (5): AWX was unavailable while creating a copy or reading a
      project. Retry later.
    - `awx`, `invalid` (1): AWX did not store a copy as the spec asks (`unverified …` fields). Rerun
      with `--keep` to inspect the copy, then fix the spec.

    Once the cases finish, each copy is deleted, workflows first, and named on
    stderr; with `--keep` it is listed as `kept … (id N)`. The results print
    after the teardown.

    - A copy teardown could not delete in 30 seconds (a job of it still runs),
      or one a second Ctrl-C left, gets a `warning: <copy>: left behind, …`
      line.
    - The warning ends with the command that deletes that run's copies:
      `untaped awx test prune --run k3x9 --older-than 0`.
    - The cases' results and the exit code stand.

    `test validate --source-ref REF` prints one `awx.provision_outcome` row per copy
    it would create (`planned`). `id` is `null` for a planned copy; `path` (`REF:PATH`
    of its spec) and `prompts` are set on planned copies only. `created_at` is when the run
    started, not when the copy was created.

    ## Leftover copies

    Copies a killed run left behind are deleted by `untaped awx test prune`
    (`untaped awx test prune --help`). It and the `validate` preview print the
    fields of a `--format json` row (`--columns '?'` lists them under
    `--dry-run`).

    ## Apply from a git ref

    `--source-ref REF` reads the given files and directories as they are at `REF`
    (a branch, tag or commit of the repository containing the current directory),
    never from the working tree, then applies them as usual:

    ```console
    $ untaped awx apply --source-ref v1.4.0 .untaped/awx/templates .untaped/awx/workflows --dry-run
    ```

    - Paths are relative to the current directory. Local edits and untracked
      files are never read.
    - `HEAD` must be pushed to its upstream (as for `awx test run --scm-branch
      HEAD`); other refs need not be, since apply reads the files locally.
    - A symbolic link at the ref is refused rather than followed. Stdin (`-`)
      cannot be combined with `--source-ref`.

    A playbook repository keeps its specs beside its suites. Folder names are only
    a convention, since each document's `kind` decides what it is:

    ```text
    .untaped/awx/
      templates/deploy.yml      # kind: JobTemplate
      workflows/release.yml     # kind: WorkflowJobTemplate
      tests/deploy-smoke.yml    # kind: AwxTestSuite
    ```
    """

    source_ref: str | None = Field(
        None,
        description=(
            "Read the files at this git ref (branch, tag or commit; HEAD once pushed) of the "
            "current repository, never the working tree."
        ),
    )


#: ``--allow-unverified``, on the commands whose writes are read back.
AllowUnverified = Annotated[
    bool, Field(description="Allow unverified configuration writes; requires --yes.")
]


class Waiting(BaseModel):
    """Read before waiting on or following what a launch or sync started: how
    --wait, --follow, --timeout and --cancel end, and which inventories sync.

    ## Syncing

    `inventories sync` updates every source of the inventory. Smart or
    source-less inventories and manual or invalid sources fail the preflight
    before any POST, so nothing starts. Results are `awx.sync_outcome` rows, as
    launches give `awx.launch_outcome` rows; pipe either into `untaped awx jobs
    wait --stdin` or `untaped awx jobs logs --stdin --follow`.

    ## Waiting and following

    - `--wait` fails the row (exit 1) on `failed`, `error` or `canceled`. The
      row gets the execution's `status`, `started_at`, `finished_at` (UTC) and
      `elapsed` (seconds).
    - With several executions, `--follow` prefixes each log line with
      `[template]`. A sliced job template's launch is a workflow job, so it
      shows status changes, not a log.
    - When AWX is still saving a job's events after it ends, a warning says the
      log may be cut short; `untaped awx jobs logs` has it all later.
    - A row `--timeout` failed names the execution in an `untaped awx jobs wait`
      hint.
    - A `--cancel` row's `detail` ends with `cancel requested`, `it ended
      (successful) before the cancel`, or `cancel failed: …`.
    - Ctrl-C exits 130 and lists the executions not known to have finished, with
      an `untaped awx jobs wait …` command to resume; they keep running.
    - With `--cancel`, Ctrl-C cancels them instead; a second Ctrl-C stops the
      cancel requests and names what may still run.
    """

    wait: bool = Field(False, description="Wait for success; fail on unsuccessful execution.")
    follow: bool = Field(
        False,
        description=(
            "Stream each job's log to stderr until it ends with its PLAY RECAP (a workflow "
            "job's status changes); fail on unsuccessful execution."
        ),
    )
    timeout: float | None = Field(
        None,
        ge=0,
        description=(
            "Stop waiting after this many seconds per execution (needs --wait or --follow); "
            "unfinished executions fail the row and keep running unless --cancel."
        ),
    )
    cancel: bool = Field(
        False,
        description=(
            "Cancel executions the wait stops watching (timeout, polling error, Ctrl-C) "
            "instead of leaving them running (needs --wait or --follow)."
        ),
    )

    def check(self) -> None:
        """``--timeout`` and ``--cancel`` only shape a ``--wait``/``--follow`` (usage errors)."""
        if self.wait or self.follow:
            return
        if self.timeout is not None:
            raise UsageError("--timeout needs --wait or --follow")
        if self.cancel:
            raise UsageError("--cancel needs --wait or --follow")


def resolve_max_depth(depth: int | None, recursive: bool) -> int | None:
    """Translate the shared ``--depth`` / ``--recursive`` flag pair.

    ``--depth N`` wins when set (``N > 0`` implies recursion);
    ``--recursive`` alone is unlimited (``None``); neither caps the
    traversal at ``0``. Used by every recursive inspector (``nodes``,
    ``usage``) so the flag semantics can't drift between commands.
    """
    if depth is not None and depth < 0:
        raise UsageError("--depth must be non-negative")
    if depth is not None:
        return depth
    return None if recursive else 0


def reject_duplicate_org_scope(organization: str | None, filters: Mapping[str, str]) -> None:
    """Refuse ``--organization`` beside a ``--filter`` that scopes by organization itself.

    ``export`` and ``lint`` build each kind's organization filter from
    ``--organization``; a raw one beside it would scope the listing twice.
    """
    if organization is None:
        return
    for key in filters:
        if _is_raw_org_scope_filter(key):
            raise UsageError(
                f"do not combine --organization with --filter {key}=...; "
                "--organization alone scopes each kind by its organization"
            )


def _is_raw_org_scope_filter(key: str) -> bool:
    return (
        key == "organization"
        or key.startswith("organization__")
        or (key == "inventory__organization" or key.startswith("inventory__organization__"))
    )


WITH_SCM_HELP = (
    "Add scm_url, effective_scm_ref (the template's scm_branch when set and its "
    "project allows the override, else the project's) and project_allow_override."
)


def offers_usage(spec: ResourceSpec) -> bool:
    """``usage`` applies to job and workflow templates, whose workflow references it looks up."""
    return spec.kind in {"JobTemplate", "WorkflowJobTemplate"}


def offers_with_scm(spec: ResourceSpec) -> bool:
    """``--with-scm`` applies to kinds that run a project's playbook."""
    return any(ref.field == "project" and ref.kind == "Project" for ref in spec.fk_refs)


__all__ = [
    "NAMES",
    "WITH_SCM_HELP",
    "AllowUnverified",
    "InventoryScope",
    "OrganizationScope",
    "ParentScope",
    "Scope",
    "Selection",
    "SourceRef",
    "Waiting",
    "Writes",
    "offers_usage",
    "offers_with_scm",
    "reject_duplicate_org_scope",
    "resolve_max_depth",
    "scope_bundle",
    "scope_values",
]
