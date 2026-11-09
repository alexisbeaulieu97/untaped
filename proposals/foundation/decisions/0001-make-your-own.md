# 0001. Goals; "make your own" is the test

- **Status:** accepted · settled before 2026-11-05 (foundation; the maintainer: "Perfect.") · the maintainer
- **Rests on:** nothing. This is the root of the tower.

## Problem

Agents had misread untaped's vision "all this time". The maintainer realized it while discussing plugin installs with another agent. That session had proposed "untaped never installs; the environment decides", then a launcher that owns its own environments. The maintainer objected: "aren't we repeating the mistake Poetry made? there has to be a better way".

The starting material was a design document another agent had written without seeing the repository. Its assessment of the repository was partly wrong:
- it missed the closed set of block kinds and the 37 SDK modules that still import `runtime`/`core`;
- it overstated "CLI-centric" and "the foundation owns selection policy";
- it gave too much weight to process isolation;
- it missed issue #1063.

The maintainer: "Let's slow wayyyyy down. I want us to agree on the foundation. The real philosophy. I actually want to go closer to deepseek harness where everything is a plugin. I want anyone to be able to install any plugin, or make their own. You don't like my implementation of the git plugin? No problem, make your own."

## Decision

**Everything is a plugin**, modelled on the DeepSeek harness (dsh). There is no privileged core to patch. Swappable capabilities are "seams": a definition package, providers, and consumers that depend on the definition, never on a provider. Plugin management is itself a plugin.

**"Make your own" is the test.** If you dislike a first-party plugin's implementation, you can replace it, and everything built on it keeps working.

Goals:
- Anyone can install any plugin or write their own, including replacements for first-party ones.
- untaped is driven from the CLI today. Later it may be driven from a TUI, GUI or web app, running locally for one person or hosted for a team.
- People can install untaped without the CLI: an MCP server for agents, or embedded in a Python program.
- The foundation must not rule out a separate project on the same kernel:
  - runners that dial out to hubs and execute jobs;
  - hubs that keep records and act as the glue;
  - front ends that launch jobs and show their execution, much like Ansible Automation Platform.

  The kernel stays extractable, but it isn't extracted now.

How decisions are made:
- **By intent, not by current demand.** Today's code shows how a decision would shift things. It never shows how many people need something: in a plugin architecture, nobody knows what people will build. The maintainer's reason: "A 'doesn't need it yet → private' classification could become a refactoring effort a week from now."
- **Get it right before others depend on it.** "Now is the cheapest it will ever be."
- **Prefer conventions** over growing lists of special cases.
- **Don't rebuild** what a good library already does.

## Consequences

Every later decision is judged by the "make your own" test and by intent. AGENTS.md's values apply throughout: unattended runs, people and agents treated alike, public interfaces only, the owner does the wiring, and no guards that only look like protection.

Sources: https://github.com/deepseek-ai/deepseek-harness (README, `docs/architecture.md` on `master`, `docs/capability-seams.md`).
