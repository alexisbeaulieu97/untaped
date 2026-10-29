# untaped docs

`untaped docs` prints what this untaped can do, as Markdown pages for
people, agents and scripts. Every fact on a page is generated from what core
and the plugins declare, or true of every untaped by construction (such as
how prompts behave without a terminal), so a page always matches the
untaped that printed it. At a terminal the pages render with colour; piped or
captured, they print as raw Markdown.

```bash
untaped docs                                  # the overview
untaped docs workspace                        # one plugin's page
untaped docs workspace commands               # its commands
untaped docs workspace commands create        # one command's page
untaped docs untaped commands config set      # core's own commands, under untaped
untaped docs github guides scope              # prose to read before using a set of options
untaped docs jira inputs issue_document       # a file you write for a command
untaped docs workspace --search archive       # every matching line, with its page
```

## Addresses

Every page's title is its address: `untaped docs`, `untaped docs PLUGIN`,
`untaped docs PLUGIN SECTION`, `untaped docs PLUGIN SECTION NAME`. Drop the
last word to go up a level. Core's own commands are the plugin `untaped`.
Commands and guides are spelled as the command line spells them; inputs and
outputs as their kind spells itself (`hello.salutation` is
`untaped docs hello outputs salutation`).

A plugin's sections, each shown only when it has something:

- `commands`: what you can run, grouped under each command group.
- `inputs`: the files you write for its commands, with the commands that read
  each one and the command that writes a starter.
- `outputs`: the rows its commands print, with the commands that print each.
- `settings`: its section of `config.yml`, every setting with its type,
  default and description.
- `extensions`: the extension points it offers other plugins, with the
  plugins that fill each, for a plugin other plugins extend.

`guides` holds the prose a plugin wants read before some of its commands; its
page lists each guide under `## Read first`, and each command that needs one
says so.

`untaped docs [ADDRESS] --search TEXT` prints every line of the pages under
the address that contains the text (ignoring case), after its page's
address. An address this untaped lacks is a usage error (exit 2) that names
the page listing what it has.

## What the pages show

- **The overview** (`untaped docs`): how the pages are organized, one entry
  per installed plugin with its line and the extension points it fills, and
  what every command shares: where settings live, tokens, output channels,
  that nothing prompts without a terminal, the "(asks first)" legend, the
  options every command takes and the exit codes.
- **A plugin's page**: its description, the guides to read first, its
  sections with how many entries each holds, and the extension points it
  fills.
- **A command's page**: its summary and behaviour, `## Usage` with every
  option `--help` shows, in the same order, `## Inputs` and `## Outputs`
  with their fields, "Read first" lines naming its guides, and its examples.
- **An input's or output's page**: its description, which commands read,
  write or print it, and its fields, one table per level of nesting.
- **An extension point's page**: who fills it, how to fill it, its methods
  and the types they take.

The line under every title names the untaped that answered: its version, the
plugin's distribution and version on a plugin's pages, and the Python
environment it runs from. When the nearest `pyproject.toml` (in the current
folder or above it, up to the git checkout's root) requires a version of
`untaped` this one does not satisfy, the next line says so and asks for the
project's own untaped instead. Every page but the overview then says where
the scheme is explained.

`[experimental]` marks what `--help` marks experimental, and a command that
takes `Confirm` says `(asks first)` on its page. Deprecated commands, options
and settings are left out, as `--help` leaves them out.

Every `--help` screen ends with the address of its page: "Full
documentation: `untaped docs ...`".

## How a plugin shapes its pages

A plugin writes nothing for the pages beyond its classes' docstrings, its
fields' descriptions and its examples (see [Plugins](../plugins.md)):

- **The plugin class's docstring** is its line on the overview (first line)
  and its page's description.
- **A command's docstring**: its first line is its summary on the commands
  list; the paragraphs after it are the behaviour on the command's page. An
  `Examples` section (NumPy style) prints under `## Examples`: put commands in
  a fenced `console` block, one per `$ ` line; `untaped lint`'s
  `example-command` rule checks that each one parses. Bare `$ ` lines are
  fenced for you, with the lines right under each as its output: a blank
  line ends an example.
- **A field's `description`** is its line under `## Usage`, under
  `## Fields` or under `settings`.
- **A record or file model's docstring** is its line on the `inputs` or
  `outputs` list (first paragraph) and its page's description.
- **A contract's docstring**: its first line is the extension point's line,
  so it says what the extension point gives the user ("Where the repos you
  can name in workspace commands come from."), not how a developer fills it;
  the rest is "How to fill it" on its page. A provider's first docstring line
  is its line under the extension point.

## Guides

A guide holds what belongs to no single command: how a set of options works
together, what the choices of an option mean, how to read a failure. It never
repeats what a command's options, a settings model or a record's model
already say.

A guide is the docstring of what it explains: a bundle of options (a
pydantic model a command takes as one field), or an enum an option takes.
Its page, `untaped docs PLUGIN guides NAME` with NAME the class name in
kebab-case, prints the docstring, then the bundle's options or the enum's
choices. Every command taking it carries "Read first:" with its address, and
the plugin's page lists it under `## Read first`. A guide's first paragraph
says when to read it.

A YAML block in a guide tagged with a record kind (`yaml acme.test_suite`)
is an example of that kind. `untaped lint` checks every guide: each
`untaped docs ...` it quotes names a page (`guide-link`), each other
`untaped ...` command parses (`guide-command`), and each kind-tagged YAML
block validates against the kind's model (`guide-yaml`). `map-line` holds a plugin's lines on the
overview (its summary, its line under an extension point it fills) and its
extension points' lines to 120 characters, since every agent reads the
overview.

## Stability

`untaped docs` and its addresses follow [Versioning](../versioning.md);
what the pages say and how they lay it out may change in any release.
Installed stub skills call it: a release that changes how they call it
refreshes the stubs in the home folder, and a project's stubs need
`untaped skills update`.
