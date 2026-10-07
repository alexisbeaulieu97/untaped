# Screens

A screen is an interactive terminal UI built on the SDK's runtime: a full
screen (or one inline question) with a footer of keys, drawn in the user's
theme. `untaped.sdk` owns the shared behavior (keys, look, the refusal when
there is no terminal, testing), so a screen declares what it shows and does
and nothing else. Screens are
[experimental](./versioning.md#experimental): the API may change in a minor
release. A plugin never imports `prompt_toolkit`; `check_conventions` flags it
(see [Enforcement](./reference/conventions.md#enforcement)).

## A screen

A screen is three pure functions plus declarations. The model is a frozen
dataclass; `update` returns the next model and the commands to run; `view`
returns a Rich renderable. `ui.run(screen)` returns what the screen quits with:

```python
from dataclasses import dataclass, replace

from rich.text import Text
from untaped.sdk import Cmd, Frame, Key, Quit, Screen, ui_context


@dataclass(frozen=True)
class Model:
    name: str = ""


def update(model: Model, message: object) -> tuple[Model, list[Cmd]]:
    match message:
        case Key("enter"):
            return model, [Cmd.send(Quit(model.name))]
        case Key(name) if len(name) == 1:
            return replace(model, name=model.name + name), []
    return model, []


def view(model: Model, frame: Frame) -> Text:
    return Text(f"name: {model.name}", style=frame.style("screen.value"))


SCREEN = Screen(
    init=lambda: (Model(), []),
    update=update,
    view=view,
    title="Name it",
    command="untaped acme name",
    alternative="untaped acme name --name NAME",
)

name = ui_context().run(SCREEN)
```

`init` returns the first model and the commands to start with (`[]` for
none); it runs once, when the screen opens. `title` names the screen: the
runtime does not draw it (draw your own heading in `view`), but it labels the
screen where a test backend records or reports one. `command` and
`alternative` are what the user sees when there is no terminal (see
[Without a terminal](#without-a-terminal)).

`ui.run` raises `OperationCancelledError` when the user backs out, and
`PromptInterruptedError` (exit 130) on an interrupt, as every prompt does. A
screen returns a value only by sending `Quit(result)`; `Cancel()` or an
unhandled esc ends it without one, and an unhandled ctrl-c ends it as an
interrupt. `layout` is `"full"` (the default: the alternate screen, the whole
terminal) or `"inline"` (below the cursor, erased when done); use inline only
for a single question.

## Messages and keys

`update` receives `Key(name)` (a printable character is its own name, a space
is `" "`; any other key is one of `up`, `down`, `left`, `right`, `home`, `end`,
`tab`, `shift-tab`, `enter`, `esc`, `backspace`, `delete`, `ctrl-u`, `ctrl-w`,
`ctrl-s`, `ctrl-c` or `ctrl-r`, the names a `Binding` and `drive_screen` also
accept), `Paste(text)`, `Resize(width, height)`, a command's message and
`CmdError(error)` when a command raised. The shared keys are the SDK's, and a
screen cannot rebind them:

| Key | Message sent | When still unhandled |
|---|---|---|
| esc | `Back` | cancels |
| ctrl-c | `Interrupt` | cancels as an interrupt |
| tab, shift-tab | `NextField`, `PrevField` | ignored |
| enter | `Activate` | ignored |
| ctrl-s | `Submit` | ignored |
| ? | `Help` (news only) | opens the help overlay |

A key goes to `update` first (where the focused component lives), then to the
screen's own `keys` (`Binding(key, label, message)`, active while its `when`
holds), then to the shared keys. So `?` typed in a text field is text, and it
opens help only when nothing took it. "Handled" means `update` returned a
different model object or any command, so return the same model for a key you
ignore. The footer and the help overlay are built from the bindings; a binding
with `message=None` only documents a key a component handles.

A screen cannot rebind a shared key, but it can say what the key does there:
`Screen(shared_labels={"ctrl-s": "create", "tab": "pane"})` relabels it in the
help overlay and adds it to the footer (the overlay otherwise says "submit" and
"next field", and the footer lists only the bindings, `esc` and `?`). A value
may be a function of the model returning the label, or `None` for the default
label and no footer entry, so `enter` can read "edit" only where it edits. It
changes only what is written, never what the key does. When the footer is too
narrow, the last entries before `esc back` and `? help` are dropped (they stay
in the overlay), so those two always show.

## Commands

A `Cmd` is a function that returns a message (or `None`). It runs off the event
loop and its message comes back to `update`:

- `Cmd(fn)` runs in the background; quitting abandons it.
- `Cmd(fn, write=True)` runs to completion even if the user quits: the footer
  reads "saving" until it finishes. One write runs at a time, in order. Use it
  for anything that must not be left half done.
- `Cmd(fn, suspend=True)` leaves the screen and gives `fn` the real terminal,
  for a password prompt or a pinentry, then redraws.
- `Cmd.send(message)` delivers a message in the same turn, with no thread.

Every command runs in the context captured when it was issued, so
`profile_scope` and the other context variables the caller set are visible in
it. A command that raises arrives as `CmdError`, never as a crash.

## Without a terminal

`ui.run` (and `ui.pick_many`, which is a screen too) uses stdin and stderr when
both are terminals. Otherwise (piped stdin, a redirected stderr) it draws on the
controlling terminal, so a screen never paints into a file. Only when none can
be opened does it fail with a usage error that names the screen's `command` and
its required `alternative`, the non-interactive way to do the same thing.
`Screen` refuses an empty one, so every screen has one. A prompt backend that
sets `needs_terminal = False` (the scripted one does) gets the screen without
any terminal being looked for.

## Drawing

A view returns a Rich renderable, and a plain `str` is literal text: markup,
emoji and highlighting are off, so a title such as `[WIP] fix [/]` prints as
is. Take every glyph, color and border from the `Frame` the view receives
(`frame.symbol(name)`, `frame.style(role)`, `frame.box()`), never a literal, so
a theme change reaches every screen. The names are declared; the
[settings reference](./reference/config.md) lists `ui.symbols` and
`ui.color_roles`. Tables and detail views do not read the `screen.*` roles.

Every glyph and colour a component draws is a token, the caret and bold
emphasis too: `screen.caret` (reverse by default) styles the caret and the
character under it, and `screen.emphasis` (bold by default) the chosen value,
the on/off symbols and the primary button's label, and `screen.match` (bold and
underlined by default) the letters a search matched, so a theme can restyle them.
Two things stay structural text and are not tokens: the `[` and `]` around a
`MultiList` mark, and the terminal's `dim` attribute, which a dimmed item layers
over the muted colour.

One color decision (`NO_COLOR`, `COLORTERM`, `TERM`) reaches both Rich and the
terminal library. Under `NO_COLOR` the cursor row keeps only bold, so a screen
must not rely on color alone.

## Components

Ready-made components are frozen dataclasses with one shape. The constructor
is the component's `init`, `update(message)` returns the component and its
commands, and `view(frame, focused=..., width=...)` draws it; the parent (the
screen, a `Tabs`) owns focus and passes it in. A screen keeps components in its
model and delegates: `field, cmds = model.name.update(message)`. Each has
`value`, `error`, `with_error(text)` and `validate()`, which returns the error
text (empty when fine) and never sets it. `Field` is that contract as a type.

A component returns the same object for a message it does not consume, so the
keys above reach the screen only when it passed: a `?` typed in a `TextInput`
is text, tab accepts a completion before it moves focus, esc closes an open
`Select` before it goes back, and enter opens one before it submits.

| Component | Holds |
|---|---|
| `TextInput` | a line of text, with an optional list of completions that opens once the user edits (a starting value is never rewritten by tab) |
| `PathInput` | a `TextInput` that completes filesystem paths (the first 500 entries of the directory) |
| `SecretInput` | a `SecretStr`, masked; the secret never reaches a frame or a `repr` |
| `NumberInput` | a number with optional bounds; `value` is the parsed number |
| `Check` | a boolean, shown as the `on` or `off` symbol alone; space toggles it, enter is left to the screen |
| `Select` | one choice, closed until enter opens it |
| `SingleList`, `MultiList` | one or many choices, all showing, as `ListItem` rows |
| `Cycle` | a value changed with left and right, or inherited |
| `Tabs`, `Tab` | tabs that each show their own fields; `value` is the active tab's |
| `Buttons`, `Button` | actions; activating one sends `Pressed(id)`. A primary button always has the bright ring and a bold label, a secondary one the muted border, a ghost one is plain text; focus is the cursor highlight on the focused button's label row |
| `SearchList` | a search box over a long list: typing filters (fuzzy, matched letters bold and underlined), enter toggles or picks |
| `Tree`, `TreeRow` | rows that expand into children; right and left open, close and step out |
| `Tags` | the chosen items as removable badges, with a `SearchList` menu to add more |
| `Form` | named fields in focus order with per-field validation; `Submit` sends `Submitted(values)` |
| `Panes` | two components in bordered panes (side by side from 100 columns, stacked below), focus moving between them |
| `Viewport` | a scrolling window over long content: which rows show, scrolling by key, following a cursor |

A `Form` hands tab and shift-tab to its parent at either end, so a `Panes` (or
your screen) takes the move after the last field; a field that consumes a key
keeps it. `Submit` (ctrl-s), or enter on a field that has no use for it, validates
every field, shows each error, focuses the first bad one and sends
`Submitted(values)` when all pass; enter on a `Buttons` row presses the button
instead. Lists (`SearchList`, `Tree`, `Tags`, and the choice lists) build only
the rows in view, so a thousand items cost no more than ten; `Viewport` does
that arithmetic (which rows show, scrolling, following a cursor) for your own
views.

`ui.pick_many`, the workspace picker, is a screen built from `Panes`,
`SearchList`, `Tree`, `TextInput` and `Buttons`; its `PickRequest` names the
`command` and `alternative` the no-terminal refusal shows (generic ones when it
leaves them unset). `allow_empty` lets it confirm with nothing selected; a
request that has a title still requires one.

`untaped setup` is a screen built from the same components: a `TextInput` for
the profile, a `SingleList` of capabilities beside a `Form` of `TextInput`,
`Tabs` (the token source) and `Buttons`, in `Panes`. It keeps its own
`shared_labels` (tab, enter and ctrl-s) and offers `Save anyway` per capability.
A command that builds its screen only after reading config or probing a store
asks `ui.require_screen_terminal(command=..., alternative=...)` first, so a run
without a terminal exits 2 having touched nothing.

Pieces it needed are public: a `Panes` side may be a drawing (a function
`(frame, focused) -> renderable`) for a pane whose model the screen owns;
`SearchList(entries=...)` takes matches the screen ranked itself, and
`SearchList`/`Tree` take `window_rows`; `Buttons.boxed_width` and
`buttons.BOX_ROWS` say what a boxed row needs. A caller that calls `ui.pick_many`
itself gets the controlling terminal when stdin is piped; `workspace create`
and `workspace add` check `ui.can_prompt` first and take their flag path
instead, so only a redirected stderr is something they now draw through.

The one-shot prompts (`ui.text`, `ui.secret`, `ui.select`, `ui.multiselect`,
`ui.confirm`) are inline screens built from `TextInput`, `SecretInput`,
`SingleList`, `SearchList` and `MultiList`, so they look and key like every
other screen. ctrl-d on an empty answer ends a prompt without one, as on a line
prompt.

`field_for(descriptor, value=..., help=...)` maps a setting's type to its
component (a `Literal` to a list or `Select`, `bool` to `Check`, numbers to
`NumberInput`, paths to `PathInput`, `SecretStr` to `SecretInput`, `str` to
`TextInput`) and raises `TypeError` naming the setting for any other type.
It reads the descriptor's `metadata`, `optional` and `description`: a number's
`ge`/`gt`/`le`/`lt` bounds are enforced, a number that is not optional cannot be
left empty while an optional one (`int | None`) may be (its value is `None`),
an optional `bool` is a three-state `Cycle` (unset, on, off; `None`, `True`,
`False`), and the field's description is the help line when you pass none.

The look comes from the theme: the label sits in the top border, the caret is
drawn, help is muted below, an error is a red border and message, and the only
fill is the highlighted cursor row. With `ui.border: none` a field is its label
line, its value and its help line.

## Testing

`untaped.testing.drive_screen(screen, keys, size=(100, 30))` runs a screen
without a terminal and returns a `ScreenRun`: the plain-text frame after each
key (`frames`), the last (`frame`), the `result`, the `outcome` and the final
`model`. Keys are names, single characters, `Paste(...)` or `Resize(...)`; any
other string raises, so a typo fails loudly. Commands run synchronously after
each key; pass `commands={name: message}` to stub one by its `Cmd.name`.

A command's own test does not run the screen: give the scripted backend a
result, a `Quit(result)`, a `Cancel()`, an exception (instance or class) to
raise or `ScreenKeys("a", "enter")` to replay through the real screen. The
scripted backend never touches a terminal, so `ui.run` and `ui.pick_many` do
not look for one:
`invoke_cli(command, args, prompt_backend=ScriptedPromptBackend(screens=[...]))`
needs no `terminal=True` and no TTY stdin.

```python
from untaped.testing import CliInvoker, ScreenKeys, ScriptedPromptBackend

backend = ScriptedPromptBackend(screens=[ScreenKeys("a", "b", "enter")])
result = CliInvoker().invoke(app, ["name"], prompt_backend=backend)
assert result.exit_code == 0
assert backend.calls == [("run_screen", "Name it")]
```
