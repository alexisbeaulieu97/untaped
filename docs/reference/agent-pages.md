# Agent pages

`untaped agents` prints what this untaped can do, as Markdown pages a coding
agent reads before running it. Every fact on a page is generated from what
core and the plugins declare, or true of every untaped by construction (such
as how prompts behave without a terminal), so a page always matches the
untaped that printed it.

```bash
untaped agents                         # the map
untaped agents workspace               # one plugin's page
untaped agents workspace create        # one command's page
untaped agents "workspace create"      # the same page
untaped agents untaped config set      # core's own commands, under untaped
```

## What the pages show

- **The map** (`untaped agents`): how untaped works, what every command
  shares (where settings live, the options every command takes, the exit
  codes), core's commands, and one section per installed plugin with its
  extension points and the plugins that fill them.
- **A plugin's page** (`untaped agents PLUGIN`): its commands with their
  marks, its extension points and who fills them, the extension points it
  fills, and its settings with their types and defaults.
- **A command's page** (`untaped agents PLUGIN COMMAND`): its summary and
  behaviour, its usage line with every option it takes, its subcommands when
  it is a group, and its examples.

The second line of every page names the untaped that answered: its version,
the plugin's distribution and version on a plugin's pages, and the Python
environment it runs from. When the nearest `pyproject.toml` (in the current
folder or above it, up to the project root) requires a version of `untaped`
this one does not satisfy, a third line says so and asks for the project's
own untaped instead. Every page but the map ends by pointing back at it.

`[experimental]` marks what `--help` marks experimental. A command that
changes something says `(changes things)`, and one that deletes or
overwrites says `(destructive)`, from what the command
[declares](./conventions.md). Deprecated commands, options and settings are
left out, as `--help` leaves them out; asked for by name, a deprecated
command's page still prints.

A name this untaped lacks is a usage error (exit 2) that names this untaped's
version and environment and points at the page that lists what it has.

## How a plugin shapes its pages

A plugin writes nothing for the pages beyond what `--help` already reads:

- **`PluginSpec.help`** (or, without one, its app's help) is the plugin's
  line on the map; its app's help is the first line of its own page.
- **A command's docstring**: its first line is its summary on the plugin's
  page; the paragraphs after it are the behaviour on the command's page.
- **An `Examples` section** in the docstring (NumPy style) prints under
  `## Examples`. Put commands in a fenced `console` block, one per `$ ` line;
  `untaped lint`'s `example-command` rule checks that each one parses.
- **Option help**: each option's `help` is its line on the command's page.
- **A contract's first docstring line** is the heading of its extension point
  on the owner's page and the map, so it says what the extension point gives
  the user ("Where the repos you can name in workspace commands come from."),
  not how a developer fills it. A provider's first docstring line is its fill
  line under that heading.
- **A setting's `description`** is its line under `## Settings`.

## What stays the same

`untaped agents` and its path argument are frozen: no release renames,
removes or deprecates them, because installed skills call them. The public
surface marks them `[frozen]`. What the pages say, and how they lay it out,
follows the plugins' own text and may change in any release.
