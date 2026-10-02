"""The starter ``AwxTestSuite`` text ``awx test init`` writes for a job template or workflow.

Pure text building from the template's ``launch/`` answer and survey questions:
required survey variables get a value (their default, their first choice, or
``TODO``; a stored password default stays ``$encrypted$``) and a comment
saying where it came from, optional ones and the enabled launch prompts are
listed as comments. A workflow's suite also lists its node ids, with a
commented ``nodes:`` expectation, and a commented ``approvals:`` answer when
it has approval nodes. Values are written as JSON
(valid YAML) and shielded from the Jinja2 rendering every suite body goes
through.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from untaped_awx.domain.workflow_run import APPROVAL, TemplateNode

TODO = "TODO"
"""The placeholder for a required value the template cannot supply."""

ENCRYPTED = "$encrypted$"
"""AWX's stand-in for a stored secret; sent back, AWX uses the stored value."""

_ASK_KEY = re.compile(r"ask_(\w+)_on_launch")
_PROMPT_FIELDS = {"variables": "extra_vars", "credential": "credentials", "tags": "job_tags"}
"""``ask_<word>_on_launch`` words that differ from the launch field they unlock."""

_JINJA_MARKERS = ("{{", "{%", "{#")


def suite_slug(name: str) -> str:
    """``Deploy app`` → ``deploy-app``: a suite and file name for a template name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "suite"


def starter_suite(
    template: str,
    *,
    organization: str | None,
    launch: Mapping[str, Any],
    survey: Sequence[Mapping[str, Any]],
    nodes: Sequence[TemplateNode] | None = None,
    approvals: Sequence[str] = (),
) -> str:
    """The commented starter suite for ``template``, one ``smoke`` case.

    With ``nodes`` (possibly none), ``template`` is a workflow; ``approvals``
    are its approval nodes' paths, nested workflows' included.
    """
    workflow = nodes is not None
    lines = [
        "# Starter suite written by `untaped awx test init` from the "
        f"{'workflow' if workflow else 'job template'}'s survey.",
        "# Edit the cases, then run `untaped awx test validate` and `untaped awx test run`;",
        "# `untaped awx schema AwxTestSuite` prints the format as a JSON Schema.",
        "kind: AwxTestSuite",
        f"name: {_value(suite_slug(template))}",
        f"{'workflowTemplate' if workflow else 'jobTemplate'}: {_value(template)}",
    ]
    if organization is not None:
        lines.append(f"organization: {_value(organization)}")
    variables = _survey_lines(survey)
    if any(not line.lstrip().startswith("#") for line in variables):
        lines += ["defaults:", "  launch:", "    extra_vars:", *variables]
    else:
        lines += [line.strip() for line in variables]
    lines += _prompt_lines(launch)
    if nodes is not None:
        lines.append(_node_comment(nodes))
    lines += ["cases:", "  smoke:"]
    if approvals:
        waits = f"approval nodes ({', '.join(approvals)})"
        lines += [
            _shield(f"    # The workflow waits on {waits}: answer them, or a pending one fails."),
            "    # approvals: approve  # or deny",
        ]
    lines += ["    expect:", "      status: successful"]
    if nodes:
        lines += ["      # nodes:", f"      #   {_value(nodes[0].label)}: {{status: successful}}"]
    return "\n".join(lines) + "\n"


def _node_comment(nodes: Sequence[TemplateNode]) -> str:
    """The workflow's node ids, each with what it runs."""
    if not nodes:
        return "# Workflow nodes: none"
    described = ", ".join(
        f"{node.label} ({'approval: ' if node.kind == APPROVAL else ''}{node.template or '?'})"
        for node in nodes
    )
    return _shield(f"# Workflow nodes (ids for expect.nodes): {described}")


def _survey_lines(survey: Sequence[Mapping[str, Any]]) -> list[str]:
    lines: list[str] = []
    for question in survey:
        variable = question.get("variable")
        if not variable:
            continue
        kind = str(question.get("type") or "text")
        choices = _choices(question.get("choices"))
        if not question.get("required"):
            lines.append(f"      # {variable}: optional survey variable ({kind})")
            continue
        comment = f"survey: required, {kind}"
        if choices:
            comment += f" [{', '.join(choices)}]"
        default = question.get("default")
        if kind == "password":
            comment += (
                " (AWX's stored default)"
                if default not in (None, "")
                else "; pass it from a secret suite variable, never write it here"
            )
        value = _survey_value(kind, default, choices)
        lines.append(f"      {variable}: {_value(value)}  # {_shield(comment)}")
    return lines


def _survey_value(kind: str, default: Any, choices: Sequence[str]) -> Any:
    """The question's default, else its first choice, else ``TODO``.

    A password's stored default is never written: AWX's ``$encrypted$``
    placeholder stands for it, and AWX puts the stored value back at launch.
    """
    if kind == "password":
        return ENCRYPTED if default not in (None, "") else TODO
    if default not in (None, ""):
        return _choices(default) if kind == "multiselect" else default
    if choices:
        return [choices[0]] if kind == "multiselect" else choices[0]
    return TODO


def _choices(raw: Any) -> list[str]:
    """Survey choices as a list: AWX stores them as a list or as newline-separated text."""
    items = raw.splitlines() if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return [str(item).strip() for item in items if str(item).strip()]


def _prompt_lines(launch: Mapping[str, Any]) -> list[str]:
    fields = [
        _PROMPT_FIELDS.get(match[1], match[1])
        for key, value in launch.items()
        if value is True and (match := _ASK_KEY.fullmatch(key))
    ]
    lines = [
        "# Launch prompts (fields a case may set under launch:): " + ", ".join(fields)
        if fields
        else "# Launch prompts: none (AWX ignores launch fields other than survey variables)"
    ]
    if launch.get("inventory_needed_to_start"):
        lines.append("# The template has no inventory: set launch.inventory to one by name.")
    if launch.get("credential_needed_to_start"):
        lines.append("# The template needs a credential: set launch.credentials to names.")
    return lines


def _value(value: Any) -> str:
    """``value`` as a YAML flow scalar or list, kept verbatim by Jinja2."""
    return _shield(json.dumps(value, ensure_ascii=False))


def _shield(text: str) -> str:
    """``text``, wrapped so that Jinja2 renders it verbatim when it looks like a template."""
    if any(marker in text for marker in _JINJA_MARKERS):
        return f"{{% raw %}}{text}{{% endraw %}}"
    return text
