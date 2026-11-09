# untaped's foundation

> Draft, being shaped with the maintainer. Settled pieces only; names in code are placeholders unless a decision says otherwise. Describes the target, not today's code.

## Why this exists

Agents kept misreading untaped's vision. This folder states the real one, so nobody has to guess it again.

**Everything is a plugin.** There is no privileged core to patch. The test is the maintainer's: *"You don't like my implementation of the git plugin? No problem, make your own"*, and everything built on it keeps working ([0001](decisions/0001-make-your-own.md)).

From [AGENTS.md](../../AGENTS.md): everything can run unattended; people and agents are treated alike; plugins know one another only through public interfaces; whoever owns a capability does its wiring, and installing a plugin that provides it is all it takes; a mechanic is solved once by its owner; no guards that only look like protection; when edge cases pile up, we settle on a convention.

How we decide:
- **By intent, never by today's usage.** Today's code shows how a decision shifts things; it is never evidence of what people will build.
- **Every decision names what it rests on.** When one of those breaks, the decision is reopened, not patched.
- **Conventions over growing lists of special cases.** Don't rebuild what a good library already does.

## Goals

- Anyone can install any plugin or write their own, including replacements for first-party ones.
- Front ends: the CLI today; later a TUI, GUI or web app, local for one person or hosted for a team. untaped also installs without the CLI: an MCP server for agents, or embedded in a Python program.
- The kernel stays extractable for a separate project: runners that dial out to hubs and execute jobs, hubs that keep records, front ends that launch jobs.

## The tower

```
 rank 4   bundles      compatible sets of packages                 ┐
 rank 3   parties      code that does things: engines, users, hosts│ design
 rank 2   grammar      how words connect                           │ (conventions and
 rank 1   words        meaning                                     ┘  first-party plugins)
────────────────────────────────────────────────────────────────────
 rank 0   kernel       how plugins find and call each other          architecture
```

Arrows point down. Replacing a piece costs what rests on it. Only the kernel is architecture: mandatory for everyone, and added to only by a deliberate decision. Everything above it is design. We keep it for ourselves so swaps keep working, and anyone may replace it ([0047](decisions/0047-architecture-vs-design.md)).

## How to read this folder

| File | What it holds |
|---|---|
| [architecture.md](architecture.md) | the kernel: its founding principle and the rules that follow from it |
| [design/conventions.md](design/conventions.md) | the buckets and their rules, and the other rules we keep for ourselves |
| [design/packaging.md](design/packaging.md) | package lines, identities, versioning, bundles, environments |
| [design/calls.md](design/calls.md) | the call grammar and the dispatcher |
| [design/plugins.md](design/plugins.md) | choosing engines: our resolver |
| [design/settings.md](design/settings.md) | settings |
| [design/hosts.md](design/hosts.md) | hosts, the run, process facts, concurrency |
| [decisions/](decisions/README.md) | one record per decision: problem, decision, what was rejected, what it rests on |
| [glossary.md](glossary.md) | the words this folder uses |

Rule pages say what is true now, in the present tense, and link the decision behind each rule. Decision records explain why, and are never rewritten: a later decision replaces or amends them.

Open questions are not kept here. They become GitHub issues ([0055](decisions/0055-document-shape.md)). A rule set aside until its topic is designed waits in its page's Pending section, and is not in force ([0056](decisions/0056-multi-host-parked.md)).
