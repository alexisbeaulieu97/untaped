"""The ``Vars`` option block: ``--var``, ``--vars-file`` and ``--non-interactive``.

A command that takes input values declares ``vars: Vars``; its docstring is
the one guide every such command links.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from untaped.sdk import ExistingFile, KeyValue, experimental


@experimental
class Vars(BaseModel):
    """Where an input's value comes from, how values are parsed, and when it prompts.

    Read before passing values to a command that takes them.

    ## Where a value comes from

    A file declares its inputs, each with a `type` and maybe a `default`. For
    each input, the first of these that gives a value wins:

    1. `--var NAME=VALUE`, then the `--vars-file` files (a later file wins
       over an earlier one);
    2. any source the command's own guide adds;
    3. the input's `default`;
    4. a prompt at a terminal, for an input that must have a value.

    A name the file doesn't declare is refused, and the message lists the
    declared ones.

    ## How values are parsed

    - `--var` gives text. A `bool` takes `1/true/yes/on` or `0/false/no/off`.
      A `list` takes a YAML list (`--var 'cols=[name, owner]'`) or
      comma-separated text (`--var cols=name,owner`); a `dict` takes a YAML
      mapping (`--var 'labels={team: core}'`).
    - `--vars-file` values are YAML, so `3.10` becomes `3.1`: quote
      version-like strings.

    ## Secrets and placeholders

    An input declared `secret: true` is asked hidden and shown as `***` in
    rows and messages. Text fields render `{{ name }}` placeholders from the
    values; a secret, `list` or `dict` input can't be placed in a field such
    as a path or a command line.

    ## Prompts

    Only an input that must have a value and has none prompts, and only when
    stdin is a terminal; `list` and `dict` inputs never prompt. With no
    terminal or with `--non-interactive`, nothing prompts and the command
    names every missing input at once.
    """

    var: list[KeyValue] = Field(
        default_factory=list,
        description="Input value as NAME=VALUE (repeatable; wins over --vars-file).",
    )
    vars_file: list[ExistingFile] = Field(
        default_factory=list,
        description="YAML mapping of input values (repeatable; later files win).",
    )
    non_interactive: bool = Field(
        False, description="Fail on missing required values instead of prompting at a terminal."
    )

    def values(self) -> dict[str, object]:
        """The ``--vars-file`` mappings in order, then the ``--var`` pairs over them."""
        from untaped.sdk.storage import read_structured_file  # noqa: PLC0415 - only when read

        merged: dict[str, object] = {}
        for path in self.vars_file:
            merged.update(read_structured_file(path, flag="--vars-file"))
        merged.update(dict(self.var))
        return merged
