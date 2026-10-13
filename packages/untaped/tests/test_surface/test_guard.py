"""The guard: each row of the design's "What happens if…" table over two small line sets."""

from __future__ import annotations

import pytest

from untaped.surface import FORMAT, Line, parse, removed


def _lines(text: str) -> list[Line]:
    return parse(f"# untaped surface {FORMAT}\n{text}")


CASES = {
    "add an optional flag": (
        "cli untaped awx jobs list",
        "cli untaped awx jobs list\ncli untaped awx jobs list --since VALUE default=unset",
        True,
    ),
    "make that flag required": (
        "cli untaped awx jobs list\ncli untaped awx jobs list --since VALUE default=unset",
        "cli untaped awx jobs list --since VALUE",
        False,
    ),
    "make a required option optional": (
        "cli untaped awx jobs list --since VALUE",
        "cli untaped awx jobs list\ncli untaped awx jobs list --since VALUE default=unset",
        True,
    ),
    "make a required option optional without its line": (
        "cli untaped awx jobs list --since VALUE",
        "cli untaped awx jobs list",
        False,
    ),
    "make a required option optional, but experimental": (
        "cli untaped awx jobs list --since VALUE",
        "cli untaped awx jobs list\ncli untaped awx jobs list --since VALUE default=unset  "
        "[experimental]",
        False,
    ),
    "a repeatable required option comes back single": (
        "cli untaped x get --tag VALUE...",
        "cli untaped x get\ncli untaped x get --tag VALUE default=unset",
        False,
    ),
    "rename a flag with a deprecated alias": (
        "cli untaped awx jobs list --template VALUE default=unset",
        "cli untaped awx jobs list --job-template VALUE default=unset\n"
        "cli untaped awx jobs list --template -> --job-template  [deprecated]\n"
        "cli untaped awx jobs list --template VALUE default=unset  [deprecated]",
        True,
    ),
    "remove that alias": (
        "cli untaped awx jobs list --template VALUE default=unset  [deprecated]",
        "cli untaped awx jobs list --job-template VALUE default=unset",
        False,
    ),
    "change a default": (
        "cli untaped x get --format VALUE default=table",
        "cli untaped x get --format VALUE default=json",
        False,
    ),
    "add a choice": (
        "cli untaped x get --format choice=json",
        "cli untaped x get --format choice=json\ncli untaped x get --format choice=jsonl",
        True,
    ),
    "add a third spelling": (
        "cli untaped x get --format alias=-f",
        "cli untaped x get --format alias=--fmt\ncli untaped x get --format alias=-f",
        True,
    ),
    "drop an alias": (
        "cli untaped x get --format alias=-f",
        "cli untaped x get --format alias=--fmt",
        False,
    ),
    "a positional becomes repeatable": (
        "cli untaped x get NAME",
        "cli untaped x get NAME...",
        True,
    ),
    "a repeatable positional becomes single": (
        "cli untaped x get NAME...",
        "cli untaped x get NAME",
        False,
    ),
    "a required positional becomes optional at its place": (
        "cli untaped x get KIND NAME",
        "cli untaped x get KIND\ncli untaped x get [NAME]@2 default=unset",
        True,
    ),
    "a required positional becomes optional elsewhere": (
        "cli untaped x get KIND NAME",
        "cli untaped x get KIND\ncli untaped x get [NAME]@3 default=unset",
        False,
    ),
    "the SDK passes a callback another argument": (
        "sdk untaped.contracts.fill(source) callback(fill, 2)",
        "sdk untaped.contracts.fill(source) callback(fill, 3)",
        False,
    ),
    "add a required field to a payload model": (
        "contract workspace.Repo(name, url)",
        "contract workspace.Repo(name, owner, url)",
        False,
    ),
    "add an optional field to a payload model": (
        "contract workspace.Repo(name, url)",
        "contract workspace.Repo(name, url)\ncontract workspace.Repo.owner string? default=None",
        True,
    ),
    "rename a stable state key": (
        "state ansible.sources array",
        "state ansible.roots array",
        False,
    ),
    "add a Literal value to a contract field": (
        "contract workspace.Repo.ref choice=heads",
        "contract workspace.Repo.ref choice=heads\ncontract workspace.Repo.ref choice=tags",
        True,
    ),
    "drop a Literal value from a contract field": (
        "contract workspace.Repo.ref choice=heads\ncontract workspace.Repo.ref choice=tags",
        "contract workspace.Repo.ref choice=heads",
        False,
    ),
    "tighten a contract field's constraint": (
        "contract workspace.Repo.name string(minLength=1)",
        "contract workspace.Repo.name string(maxLength=64,minLength=1)",
        False,
    ),
    "deprecate a stable command": (
        "cli untaped awx jobs list",
        "cli untaped awx jobs list  [deprecated]",
        True,
    ),
    "mark a stable command experimental": (
        "cli untaped awx jobs list",
        "cli untaped awx jobs list  [experimental]",
        False,
    ),
    "promote experimental to stable": (
        "cli untaped workspace sync  [experimental]",
        "cli untaped workspace sync",
        True,
    ),
    "remove anything experimental": (
        "cli untaped workspace sync  [experimental]\ncfg workspace.parallel integer? "
        "default=unset  [experimental]",
        "",
        True,
    ),
    "add an optional sdk parameter": (
        "sdk untaped.contracts.gather(method) callback(method, P)",
        "sdk untaped.contracts.gather(method) callback(method, P)\n"
        "sdk untaped.contracts.gather(trace=) default=false",
        True,
    ),
    "insert a positional-capable parameter before another": (
        "sdk untaped.sdk.ProgressHandle.update(message@1=) default=None",
        "sdk untaped.sdk.ProgressHandle.update(message@2=) default=None\n"
        "sdk untaped.sdk.ProgressHandle.update(phase@1=) default=None",
        False,
    ),
    "add a method to a caller-shaped class": (
        "sdk untaped.sdk.ProgressHandle.update()",
        "sdk untaped.sdk.ProgressHandle.done()\nsdk untaped.sdk.ProgressHandle.update()",
        True,
    ),
    "add an abstract method to a contract": (
        "contract workspace.repo_source abstract=repos",
        "contract workspace.repo_source abstract=repos,teams",
        False,
    ),
    "give a contract method parameter a default": (
        "contract workspace.repo_source.repos(refresh: boolean?) -> Repo[]",
        "contract workspace.repo_source.repos(refresh: boolean? = None) -> Repo[]",
        False,
    ),
    "retype a payload field": (
        "contract workspace.Repo.url string",
        "contract workspace.Repo.url object",
        False,
    ),
    "remove a setting key": ("cfg http.timeout number? default=unset", "", False),
    "rename a setting with renamed_keys": (
        "cfg http.timeout_secs number? default=unset",
        "cfg http.timeout number? default=unset\n"
        "cfg http.timeout_secs -> http.timeout  [deprecated]\n"
        "cfg http.timeout_secs number? default=unset  [deprecated]",
        True,
    ),
    "remove an SDK export": ("sdk untaped.sdk.bounded_map(func, items)", "", False),
    "rename a class attribute the SDK reads": (
        "sdk untaped.sdk.UntapedError.category class",
        "sdk untaped.sdk.UntapedError.kind class",
        False,
    ),
    "drop a negative spelling": (
        "cli untaped ansible source refresh --refresh negative=--cached",
        "",
        False,
    ),
    "renumber an enum member": (
        "sdk untaped.sdk.ExitCode.USAGE = 2",
        "sdk untaped.sdk.ExitCode.USAGE = 64",
        False,
    ),
    "a gathered callback becomes optional": (
        "sdk untaped.contracts.gather(method, *, timeout) callback(method, P)",
        "sdk untaped.contracts.gather(*, timeout)\n"
        "sdk untaped.contracts.gather(method@1=) default=None callback(P)",
        True,
    ),
    "a callback becomes optional with another arity": (
        "sdk untaped.contracts.gather(method) callback(method, P)",
        "sdk untaped.contracts.gather()\n"
        "sdk untaped.contracts.gather(method@1=) default=None callback(*)",
        False,
    ),
    "drop a parameter that never comes back": (
        "sdk m.f(a, b)",
        "sdk m.f(a)",
        False,
    ),
    "drop a positional and make the other optional": (
        "sdk m.f(a, b)",
        "sdk m.f(b)\nsdk m.f(a@2=) default=None",
        False,
    ),
    "a required keyword becomes optional": (
        "sdk m.f(a, *, b)",
        "sdk m.f(a)\nsdk m.f(b=) default=None",
        True,
    ),
    "a parameter becomes positional-only": (
        "sdk m.f(a)",
        "sdk m.f(a, /)",
        False,
    ),
    "an implementer's method loses a required parameter": (
        "sdk m.Backend implement=ask\nsdk m.Backend.ask(question: string, hint: string) -> string",
        "sdk m.Backend implement=ask\nsdk m.Backend.ask(question: string) -> string\n"
        "sdk m.Backend.ask(hint=) default=None",
        False,
    ),
    "an implementer's constructor loses a required parameter": (
        "sdk m.Backend implement=ask\nsdk m.Backend(a, b)",
        "sdk m.Backend implement=ask\nsdk m.Backend(a)\nsdk m.Backend(b@2=) default=None",
        False,
    ),
    "a convention goes": ("convention file fills.json", "", False),
}


@pytest.mark.parametrize(("old", "new", "passes"), list(CASES.values()), ids=list(CASES))
def test_the_guard(old: str, new: str, passes: bool) -> None:
    gone = removed(_lines(old), _lines(new))
    assert (gone == []) is passes, gone


def test_removed_lists_each_gone_line_in_file_order() -> None:
    old = _lines("cli untaped b\ncli untaped a\ncli untaped c  [experimental]")
    assert removed(old, []) == [Line("cli untaped a"), Line("cli untaped b")]
