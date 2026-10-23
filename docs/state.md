# Plugin state

State is data a plugin keeps as the only copy: what a user created through its
commands (a workspace, a saved source, an alias), which no upstream can give
back. It lives in the [state file](./configuration.md#file-and-layout)
(`~/.untaped/state.yml`), under the plugin's own section, outside profiles, and
is never a user setting. Data your plugin can fetch again is a
[cache](./caches.md) instead.

## Typed rows

`StateRows` (experimental) keeps rows of one pydantic model, one per key:

```python
from untaped.sdk import PluginSpec, StateRows

ITEMS = StateRows(
    "acme",
    "items",
    model=Item,
    key_field="name",
    remove_hint="untaped acme remove {key}",
)
SPEC = PluginSpec(name="acme", stores=(ITEMS,))

ITEMS.add(Item(name="one"))                       # RowExists (exit 1) when "one" has a row
ITEMS.get("one")                                  # the row, or None
ITEMS.update("one", lambda item: item.model_copy(update={"label": "First"}))
ITEMS.delete("one")
```

The rows are `acme.items` in `state.yml`, a list of mappings. `model` is the
class, or a function of no arguments returning it when importing the class
would load more than a declaration should. `key_field` names a required `str`
field. A row is written `mode="json"`, by alias; `omit_none=True` leaves its
`None` fields out.

- `get(key)`, `all()` (file order) and `keys()` read; `add`, `put` (replace in
  place, or append), `update(key, change)`, `replace_all(change)` and
  `delete(key)` write, each under the state file's lock. Change a row with
  `update`, never `get` then `put`: another process may write in between.
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
change to it stop with exit 4, naming the file, the row and the fix (the
`remove_hint` command, when you give one). `keys()`, the other rows and
`delete` keep working, so the user can remove it. `untaped doctor` checks every
row in the home, and the current project's.

## A project's rows

Rows a project keeps live in its own `.untaped/state.yml` and move with its
folder:

```python
CHECKOUTS = StateRows.per_project("acme", "checkouts", model=Checkout, key_field="key")
SPEC = PluginSpec(name="acme", stores=(CHECKOUTS,))

CHECKOUTS.of(project).put(Checkout(key="api", ...))
```

`per_project` returns the declaration; `.of(project)` opens one project's
rows, with every method above. `project remove` deletes the project's
`state.yml`. A string map takes `StateMap("acme", "aliases", project=project)`.

## String maps and dict rows

`StateMap(section, key)` keeps a flat `str → str` map; `StateCollection(section,
key, id_field=)` keeps rows as the dicts you pass, a row without its id field
never matched. Both are stable; prefer `StateRows` for new rows.

## Declaring state

List every state declaration on `PluginSpec.stores`: each one's name is a key
of your state section, so no setting may take it, and `untaped config get` and
`set` refuse it. A `StateRows` made in the module that makes your spec, or in
the package whose `__init__` makes it, but not listed quarantines the plugin
(`unlisted-state`), as does a declaration of another plugin's state
(`foreign-store`) or a name listed twice (`duplicate-store`).

A plugin may also declare its whole section as a model, `PluginSpec(state=)`;
the config reference and `doctor` read both. A key a declaration owns is read
only through it, never with the section's model.

## Tests

`untaped.sdk.testing` writes and reads rows unchecked: `write_rows(ITEMS, [...])`
stores what a newer install or a damaged file would, `read_rows(ITEMS)` returns
what is stored, and `damage(ITEMS, "one")` damages one row.
