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
untaped agents PLUGIN GUIDE            # a guide the plugin wrote
```

## What the pages show

- **The map** (`untaped agents`): how untaped works, what every command
  shares (where settings live, the options every command takes, the exit
  codes), core's commands, and one section per installed plugin with its
  extension points and the plugins that fill them.
- **A plugin's page** (`untaped agents PLUGIN`): its commands with their
  marks, its extension points and who fills them, the extension points it
  fills, its [guides](#guides) with when to read each, and its settings
  with their types and defaults.
- **A command's page** (`untaped agents PLUGIN COMMAND`): its summary and
  behaviour, its usage line with every option it takes, its subcommands when
  it is a group, and its examples.
- **A guide's page** (`untaped agents PLUGIN GUIDE`): the guide's text. A
  command and a guide share one list of topics; a command wins over a guide
  of the same name, and the plugin's page lists such a guide as shadowed.

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

Beyond its [guides](#guides), a plugin writes nothing for the pages that
`--help` doesn't already read:

- **`PluginSpec.help`** (or, without one, its app's help) is the plugin's
  line on the map; its app's help is the first line of its own page.
- **A command's docstring**: its first line is its summary on the plugin's
  page; the paragraphs after it are the behaviour on the command's page. A
  plugin whose app runs on its own (`@app.default`, as `untaped lint`) shows
  that command's docstring, usage and examples under `## Usage` on its own
  page.
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

## Guides

A guide holds what belongs to no single command: how a lifecycle runs, a
file format explained, how to read a failure. It never repeats what a
command's options, a settings model or a record's schema already say.

Point `PluginSpec.guides` at a folder of Markdown files inside the package,
read the way package data is, so the guides survive a zip import:

```python
from importlib.resources import files

SPEC = PluginSpec(name="acme", ..., guides=files("untaped_acme") / "guides")
```

Each `*.md` file directly in the folder is one guide, named by its stem
(`test-suites.md` is `untaped agents acme test-suites`). It opens with front
matter whose one-line `description` says when to read it; the page writes
the heading, so the text starts its own headings at `##`:

````markdown
---
description: "Read before writing a test suite: its files, cases and checks."
---

A suite is one YAML file per job template. ...

```yaml acme.test_suite
name: deploy
```
````

A guide links another by its command line (`untaped agents acme other`).
A YAML block tagged with a record kind (`yaml acme.test_suite`) is an example
of that kind.

`untaped lint` checks every guide: its name is kebab-case and no top-level
command's (`guide-name`), it has the description (`guide-description`) and no
level-one heading (`guide-heading`), each `untaped agents ...` it quotes
names a page (`guide-link`), each other `untaped ...` command parses
(`guide-command`), and each kind-tagged YAML block validates against the
kind's model (`guide-yaml`). `map-line` holds a plugin's lines on the map
(its `help`, its contracts' and providers' first lines) to 120 characters,
since every agent reads the map.

## What stays the same

`untaped agents` and its path argument are frozen: no release renames,
removes or deprecates them, because installed skills call them. The public
surface marks them `[frozen]`. What the pages say, and how they lay it out,
follows the plugins' own text and may change in any release.
