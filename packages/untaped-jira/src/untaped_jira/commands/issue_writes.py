"""``untaped jira issues create|patch|comment|transition``: the issue writes.

Each previews the requests it sends under ``--dry-run``; patches and
transitions ask first when they replace or remove. The ``issues`` group's
page explains both.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from pydantic import Field

from untaped.sdk import (
    ConfigError,
    Confirm,
    ExistingFile,
    File,
    KeyJson,
    KeyValue,
    Output,
    Positional,
    Stdin,
    TextOrStdin,
    UntapedError,
    UsageError,
    attribution,
    not_found,
    plural,
    q,
)
from untaped_jira.changes import (
    change_line,
    comment_lines,
    patch_replaces,
    payload_changes,
    referenced_fields,
    transition_changes,
)
from untaped_jira.client import JiraClient
from untaped_jira.commands._jira import (
    ISSUE_KEY,
    AsksFirst,
    JiraCommand,
    JiraWrite,
    PreviewRequest,
    SendsDirectly,
    issue_keys,
    request_lines,
)
from untaped_jira.errors import JiraError, JiraTransitionError
from untaped_jira.keys import validate_issue_key
from untaped_jira.parse import browse_url, parse_user
from untaped_jira.payloads import (
    IssueDocument,
    build_assignee_payload,
    build_issue_payload,
    build_transition_payload,
)
from untaped_jira.records import Issue, IssueOutcome


class _Fields(JiraWrite):
    """The field flags both ``create`` and ``patch`` take, over an optional fields file."""

    fields_file: File[IssueDocument] | None = Field(
        None, description="Jira-shaped YAML/JSON document (fields/update); flags override it."
    )
    summary: str | None = Field(None, description="Issue summary.")
    description: str | None = Field(None, description="Issue description text.")
    set_: list[KeyValue] = Field(
        default_factory=list, description="Set a string field KEY=VALUE (repeatable)."
    )
    set_json: list[KeyJson] = Field(
        default_factory=list, description="Set a field from JSON KEY=JSON (repeatable)."
    )

    def _payload(self, **fields: str | None) -> dict[str, Any]:
        base = self.fields_file.load().payload() if self.fields_file is not None else {}
        return build_issue_payload(
            base=base,
            summary=self.summary,
            description=self.description,
            fields=dict(self.set_),
            json_fields=dict(self.set_json),
            **fields,
        )


class Create(_Fields, SendsDirectly):
    """Create one issue from flags and an optional Jira-shaped fields file.

    Flags override the fields file. It never asks; ``--dry-run`` previews it,
    as the ``issues`` group's page explains.
    """

    project: str | None = Field(None, description="Project key; defaults to jira.default_project.")
    issue_type: str | None = Field(None, description="Issue type name (e.g. Bug, Task).")

    def run(self) -> IssueOutcome:
        settings = self.settings
        payload = self._payload(
            project=self.project or settings.default_project, issue_type=self.issue_type
        )

        def send(client: JiraClient) -> IssueOutcome:
            created = client.create_issue(payload)
            key = _text(created.get("key"))
            return IssueOutcome(
                action="created",
                key=key,
                id=_text(created.get("id")),
                url=browse_url(settings.url, key) if key else None,
                api_url=_text(created.get("self")),
            )

        return self.send(
            [("POST", f"{settings.api_prefix}/issue", payload_changes(payload))],
            send,
            planned=IssueOutcome(action="planned"),
            progress="Creating issue",
        )


class Patch(_Fields, AsksFirst):
    """Update fields of one issue (including its assignee) from flags or a fields file.

    ``--assignee`` and ``--unassign`` override ``fields.assignee`` in a
    fields file.

    The assignment goes through Jira's assignee endpoint, so it works even
    when the assignee field is not on the issue's edit screen. A patch that
    changes fields and the assignee sends the field edit first; if only the
    assignment fails, the error says the fields were already updated. The
    ``issues`` group's page explains the preview and when it asks first.

    Examples
    --------
    ```console
    $ untaped jira issues patch OPS-123 --assignee @me --set-json 'labels=["infra","tls"]' --dry-run
    ```
    """

    key: Positional[str] = Field(description=ISSUE_KEY)
    summary: str | None = Field(None, description="New issue summary.")
    description: str | None = Field(None, description="New issue description text.")
    assignee: str | None = Field(None, description="Assign to this username, or @me for yourself.")
    unassign: bool = Field(False, description="Remove the assignee.")

    def run(self) -> IssueOutcome:
        key = validate_issue_key(self.key)
        settings = self.settings
        edit, assign = self._requests()

        def preview(client: JiraClient) -> list[PreviewRequest]:
            names = referenced_fields(edit) + (["assignee"] if assign is not None else [])
            current = (client.get_issue(key, fields=names).get("fields") or {}) if names else {}
            requests: list[PreviewRequest] = []
            if edit is not None:
                path = f"{settings.api_prefix}/issue/{key}"
                requests.append(("PUT", path, payload_changes(edit, current)))
            if assign is not None:
                line = change_line("assignee", assign, old=current.get("assignee"))
                requests.append(("PUT", f"{settings.api_prefix}/issue/{key}/assignee", [line]))
            return requests

        def send(client: JiraClient) -> IssueOutcome:
            if edit is not None:
                client.edit_issue(key, edit)
            if assign is not None:
                try:
                    client.assign_issue(key, assign)
                except JiraError as err:
                    if edit is None:
                        raise
                    raise JiraError(
                        f"fields updated, but assigning failed: {err}", **attribution(err)
                    ) from err
            return IssueOutcome(action="updated", key=key, url=browse_url(settings.url, key))

        return self.send(
            preview,
            send,
            replaces=patch_replaces(edit, assigns=assign is not None),
            planned=IssueOutcome(action="planned", key=key, url=browse_url(settings.url, key)),
            progress="Updating issue",
        )

    def _requests(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """The field edit and the assignment to send; ``None`` for one not asked for."""
        payload = self._payload()
        assigning = self.assignee is not None or self.unassign
        if self.assignee is not None and self.unassign:
            raise UsageError("pass either --assignee or --unassign, not both")
        if assigning:
            payload["fields"].pop("assignee", None)  # the flag overrides the fields file
        has_fields = bool(payload.get("fields") or payload.get("update"))
        if not has_fields and not assigning:
            raise UsageError(
                "nothing to update: pass --summary, --description, --assignee, --unassign, "
                "--set, --set-json, or a --fields-file with fields/update"
            )
        assign = None
        if self.assignee is not None:
            assign = build_assignee_payload(self._username(self.assignee))
        elif self.unassign:
            assign = build_assignee_payload(None)
        return (payload if has_fields else None), assign

    def _username(self, assignee: str) -> str:
        """``assignee`` itself, or the authenticated user's name for ``@me`` (one GET)."""
        if assignee != "@me":
            return assignee
        with self.client() as client, self.ctx.diag.progress("Fetching authenticated user"):
            name = parse_user(client.me()).name
        if not name:
            raise JiraError("the authenticated Jira user has no username")
        return name


class Comment(SendsDirectly):
    """Add a comment to one issue; ``--body -`` reads it from stdin.

    Examples
    --------
    ```console
    $ git log -1 --format=%B | untaped jira issues comment OPS-123 --body -
    ```
    """

    key: Positional[str] = Field(description=ISSUE_KEY)
    body: TextOrStdin | None = Field(None, description="Comment body, or - to read it from stdin.")
    body_file: ExistingFile | None = Field(None, description="Read the comment body from a file.")

    def run(self) -> IssueOutcome:
        key = validate_issue_key(self.key)
        settings = self.settings
        if self.body is not None and self.body_file is not None:
            raise UsageError("pass --body or --body-file, not both")
        if self.body is not None:
            body = self.body  # a pipe's last newline is already off
        elif self.body_file is not None:
            # a file ends with a newline that is not the comment's
            body = _without_last_newline(self.body_file.read_text(encoding="utf-8", newline=""))
        else:
            raise UsageError("a comment needs --body (- reads stdin) or --body-file")
        if not body.strip():
            raise ConfigError(
                "no body provided (use --body, --body-file, or --body -)", category="invalid"
            )

        def send(client: JiraClient) -> IssueOutcome:
            result = client.add_comment(key, body)
            return IssueOutcome(
                action="commented",
                key=key,
                url=browse_url(settings.url, key),
                comment_id=_text(result.get("id")),
            )

        return self.send(
            [("POST", f"{settings.api_prefix}/issue/{key}/comment", comment_lines(body))],
            send,
            planned=IssueOutcome(action="planned", key=key, url=browse_url(settings.url, key)),
            progress="Adding comment",
        )


class Transition(JiraCommand):
    """Apply one workflow transition, by name or id, to one or more issues.

    Pass exactly one of ``--to`` or ``--id``, as ``issues transitions KEY``
    lists them for the issue's current status. ``--resolution`` and
    ``--comment`` go in the same request as the transition.

    One key prints one record. Several keys, or ``--stdin`` with bare keys
    or ``jira.issue`` and ``jira.issue_outcome`` records from ``--format
    pipe``, transition as one batch and print rows: a key that fails prints
    ``error: KEY: ...``, the rest continue, and the exit code is the most
    severe failure. The ``issues`` group's page explains the preview and
    when it asks first.

    Examples
    --------
    Every issue of a search, previewed as one batch (piping one command into
    the other works the same):

    ```console
    $ untaped jira issues search --project OPS --status 'In Review' -f pipe > review.pipe
    $ untaped jira issues transition --stdin --to Done --dry-run < review.pipe
    ```
    """

    keys: Positional[list[str]] = Field(
        default_factory=list,
        description="Issue keys (PROJECT-123, in any case) or numeric ids, or pass --stdin.",
    )
    to: str | None = Field(None, description="Transition name.")
    id: str | None = Field(None, description="Transition id.")
    comment: str | None = Field(None, description="Add this comment with the transition.")
    resolution: str | None = Field(None, description="Set this resolution (e.g. Fixed, Done).")
    stdin: Stdin[Issue | IssueOutcome]
    confirm: Confirm
    output: Output[IssueOutcome] = Output(columns=("key", "transition_id", "action"))

    def run(self) -> IssueOutcome | Iterator[IssueOutcome]:
        if bool(self.id) == bool(self.to):
            raise UsageError("provide exactly one of --id or --to")
        rows = self._rows(issue_keys(self.stdin.identifiers(self.keys)))
        if len(self.keys) == 1 and len(rows) == 1:
            return rows[0]  # one key prints one record, as ``issues get KEY`` does
        return iter(rows)

    def _rows(self, keys: Sequence[str]) -> list[IssueOutcome]:
        url = self.settings.url
        with self.client() as client:
            with self.ctx.diag.progress("Resolving transitions"):
                plans = [(key, plan) for key in keys if (plan := self._plan(client, key))]
            if not plans:
                return []
            question = f"Transition {plural(len(plans), 'issue')} in Jira?"
            preview = lambda: self._preview(client, plans)  # noqa: E731 - read only when asked
            if not self.confirm(question, preview=preview):
                return [
                    IssueOutcome(
                        action="planned",
                        key=key,
                        url=browse_url(url, key),
                        transition_id=plan["id"],
                    )
                    for key, plan in plans
                ]
            return [self._send(client, key, plan["id"]) for key, plan in plans]

    def _plan(self, client: JiraClient, key: str) -> dict[str, Any] | None:
        """The transition to apply to ``key``: ``{"id": ...}`` for an id, else looked up by name.

        A transition found by name keeps Jira's ``name`` and ``to`` (its
        target status), so the preview need not list the transitions again.
        A key that fails is reported and left out.
        """
        if self.id:
            return {"id": self.id}
        try:
            return _named(client, key, self.to or "")
        except UntapedError as exc:
            self.ctx.diag.failure(exc, item=key)
            return None

    def _preview(
        self, client: JiraClient, plans: Sequence[tuple[str, Mapping[str, Any]]]
    ) -> list[str]:
        prefix = self.settings.api_prefix
        with self.ctx.diag.progress("Reading current values"):
            requests: list[PreviewRequest] = [
                ("POST", f"{prefix}/issue/{key}/transitions", self._changes(client, key, plan))
                for key, plan in plans
            ]
        return request_lines(requests)

    def _changes(self, client: JiraClient, key: str, transition: Mapping[str, Any]) -> list[str]:
        """A transition's lines: one read of the issue's status and resolution.

        When ``transition`` came from an id (without its target), the same
        read lists what the issue offers. A failed read shows ``(unknown)``
        rather than failing the batch.
        """
        known = "to" in transition
        try:
            issue = client.get_issue(
                key, fields=["status", "resolution"], expand=None if known else "transitions"
            )
        except UntapedError:
            return transition_changes(
                transition, None, comment=self.comment, resolution=self.resolution
            )
        match, available = transition, True
        if not known:
            offered = {str(t.get("id")): t for t in issue.get("transitions") or []}
            found = offered.get(str(transition["id"]))
            match, available = found or transition, found is not None
        return transition_changes(
            match,
            issue.get("fields") or {},
            available=available,
            comment=self.comment,
            resolution=self.resolution,
        )

    def _send(self, client: JiraClient, key: str, transition_id: str) -> IssueOutcome:
        payload = build_transition_payload(
            transition_id, comment=self.comment, resolution=self.resolution
        )
        url = browse_url(self.settings.url, key)
        try:
            with self.ctx.diag.progress(f"Transitioning {key}"):
                client.transition_issue(key, payload)
        except UntapedError as exc:
            error = self.ctx.diag.failure(exc, item=key)
            return IssueOutcome(
                action="failed", key=key, url=url, transition_id=transition_id, error=error
            )
        return IssueOutcome(action="transitioned", key=key, url=url, transition_id=transition_id)


def _named(client: JiraClient, key: str, name: str) -> dict[str, Any]:
    transitions = client.list_transitions(key)
    matches = [t for t in transitions if str(t.get("name", "")).casefold() == name.casefold()]
    if not matches:
        known = sorted({str(t.get("name", "")) for t in transitions})
        raise JiraTransitionError(
            f"{not_found('transition', name, known=known)} (issue {key})", category="not_found"
        )
    if len(matches) > 1:
        raise JiraTransitionError(f"multiple transitions named {q(name)} are available for {key}")
    return {**matches[0], "id": str(matches[0]["id"])}


def _without_last_newline(text: str) -> str:
    """``text`` less one terminal newline (LF or CRLF), as a file or a pipe ends."""
    return text.removesuffix("\n").removesuffix("\r") if text.endswith("\n") else text


def _text(value: Any) -> str | None:
    return None if value is None else str(value)
