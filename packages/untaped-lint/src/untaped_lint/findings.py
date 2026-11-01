"""Which findings a run reports: the rule ids, ignores and allow markers a lint command takes."""

from __future__ import annotations

from pydantic import BaseModel, Field

from untaped_lint.domain.selection import parse_rule, parse_suppression


class Findings(BaseModel):
    """Rule ids, targets, ignores and allow markers: which findings a run reports.

    Read when a run reports findings or one must be left out.

    ## Rule ids

    A finding's `rule` is its public id, `<key>.<id>`: the key is the plugin that
    declares the rule (`lint` for lint's own) or the repository's own key (`name:`
    in `.untaped/lint.yml`, as `house`), and the id is what the rule declares
    (`error-case`). The bare id is accepted wherever exactly one loaded rule
    carries it; a bare id two keys declare names neither. `untaped lint rules`
    lists every loaded rule with what it reads, its severity and its summary.

    A finding's `target` is what it is about, spelled by its owner: a file's path,
    a plugin's name. Its `location`, when it has one, is the file and line.

    ## Leaving a finding out

    A suppression is `<rule>[:<target-glob>]`, spelled the same in each place it
    can be written, and the places add up:

    | Where | For |
    |---|---|
    | `lint.ignore` (a list) in `config.yml` | every run (in `profiles.<name>`, that profile's) |
    | `ignore:` in `.untaped/lint.yml` | every run in that repository |
    | `--ignore` (repeatable) | one run |

    In the glob `*` stops at `/` and `**` crosses it. The line a finding points
    at may carry `# untaped: allow <rule>` (the bare id or `<key>.<id>`), which
    allows that rule there; a bare id two keys declare allows nothing, with a
    warning. An `ignore:` or `lint.ignore` entry, or a marker, hides the finding
    from every later run; `--ignore` hides it from one. An id no loaded rule
    declares is a warning, not an error: the plugin that owns it may not be
    installed, or its key may be missing from the lint file. `untaped doctor`
    checks the profile's list (`lint.ignore-ids`).

    ## What narrowing never hides

    `--select` and PATHS narrow what is reported, never what is read. They never
    drop `lint.plugin-not-installed` or `lint.quarantined`, which say no rule ran
    on a plugin, or `lint.unreadable-file`, which says a file was not read; an
    ignore that names one of these rules does drop it. A rule or provider that
    raises is a failure on stderr for that rule (exit 1); the other rules still
    run, and a failed rule is never a pass.
    """

    select: list[str] = Field(
        default_factory=list,
        description="Report only these rules (repeatable), as lint.error-case.",
    )
    ignore: list[str] = Field(
        default_factory=list,
        description="Leave out a rule (repeatable), everywhere or where the target matches: "
        "RULE[:GLOB].",
    )
    strict: bool = Field(False, description="Fail (exit 3) on warnings too.")

    def check(self) -> None:
        """Refuse a misspelled ``--select`` or ``--ignore`` before anything runs (exit 2)."""
        for each in self.select:
            parse_rule(each, option="--select")
        for each in self.ignore:
            parse_suppression(each, option="--ignore")
