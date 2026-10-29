# Plugin state

State is data a plugin keeps as the only copy: what a user created through its
commands (a workspace, a saved source, an alias), which no upstream can give
back. Each plugin keeps it in its own file, `~/.untaped/plugins/<plugin>/state.yml`,
outside profiles; it is never a user setting. Data your plugin can fetch again
is a [cache](./caches.md) instead.

## Typed rows

`StateRows` (experimental) keeps rows of one pydantic model. Your plugin class
holds it, and the attribute names the rows:

```python
from typing import Annotated

from pydantic import BaseModel, Field

from untaped.sdk import Command, Key, Plugin, StateRows
from untaped.sdk.fields import Positional


class AcmeSettings(BaseModel):
    pass


class Item(BaseModel):
    name: Annotated[str, Key]
    label: str = ""


ITEMS = StateRows[Item]()


class Remove(Command):
    """Remove an item."""

    removes = ITEMS

    name: Positional[str] = Field(description="The item to remove.")

    def run(self) -> None:
        ITEMS.delete(self.name)


class Acme(Plugin):
    """Keep items."""

    settings = AcmeSettings
    commands = (Remove,)
    items = ITEMS


# inside a command, or any code its run reaches:
ITEMS.add(Item(name="one"))                       # RowExists (exit 1) when "one" has a row
ITEMS.get("one")                                  # the row, or None
ITEMS.update("one", lambda item: item.model_copy(update={"label": "First"}))
ITEMS.delete("one")
```

The rows are `items` in `plugins/acme/state.yml`, a list of mappings; the file
keeps its stamp under `untaped:`, so no rows may take that name. The row's key
is its one required `str` field marked `Key`. A model with no field marked
`Key` makes the rows a list: `all()`, `append(row)` and `replace_all(change)`,
with no key to look a row up by. A row is written `mode="json"`, by alias;
`StateRows[Item](omit_none=True)` leaves its `None` fields out.

- `get(key)`, `all()` (file order) and `keys()` read; `add`, `put` (replace in
  place, or append), `update(key, change)`, `replace_all(change)` and
  `delete(key)` write, each under the file's lock. Change a row with `update`,
  never `get` then `put`: another process may write in between.
- A write from inside `update`'s or `replace_all`'s `change` is refused and
  nothing is written: read first, then write once.
- A full disk is an error (exit 4), and the file stays as it was.

**A newer untaped's fields are kept.** Two installs can share one home. Rows
are read with `extra="ignore"`, nested models included, so a field a newer
untaped added never fails yours; when you write the row back, every field you
didn't change keeps what was stored, and the fields your model doesn't know
stay. A changed nested model keeps them one level down; a changed list keeps
each unchanged element as stored.

So a field you add to a row's model needs a default that means the same as the
field being absent: older releases write rows without it. The public surface
lists each top-level field of a stable plugin's rows (`state acme.items.label
string default=""`), and a minor release refuses a new one without a default;
a nested model's fields aren't listed, so give each new one a default too.

**A damaged row stops the command.** A row that fails the model, has no key,
or shares its key with another is never guessed at: reading it, `all()` and a
change to it stop with exit 4, naming the file, the row and the fix. `keys()`,
the other rows and `delete` keep working, so the user can remove it.
`untaped doctor` checks every row in the home, and the current project's.

## Removing a row

The command that removes rows marks itself `removes = ITEMS`; a damaged row's
error then offers it with the row's key (`untaped acme remove one`). It takes
the key under the `Key` field's name, as its first positional or as an option
(`--name KEY`); composition quarantines any other remover (`remover-key`).
One command removes a plugin's rows: a second one is `duplicate-remover`, and a
`removes` naming rows the plugin class doesn't hold is refused too.

## A project's rows

Rows a project keeps live in its own `.untaped/plugins/<plugin>/state.yml` and
move with its folder:

```python
class Acme(Plugin):
    checkouts = StateRows[Checkout].per_project()

Acme.checkouts.of(project).put(Checkout(key="api", ...))
```

`per_project` returns the declaration; `.of(project)` opens one project's
rows, with every method above. `project remove` deletes them with the rest of
what untaped wrote in the project's `.untaped` folder.

## Tests

`untaped.sdk.testing` writes and reads rows unchecked: `write_rows(ITEMS, [...])`
stores what a newer install or a damaged file would, `read_rows(ITEMS)` returns
what is stored, and `damage(ITEMS, "one")` damages one row. Each needs a run of
the plugin: `with install(Acme).running():`.
