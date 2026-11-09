# 0015. The builder's record is the only plugin list

- **Status:** accepted · settled before 2026-11-05 (7.4) · the maintainer
- **Rests on:** [0010](0010-one-process.md), one process
- **Reopen if:** a builder (uv tool, pipx, a project, …) can't expose enough to edit its list from an installer engine, or the Windows hand-off can't be made to work. The rejected option (untaped owns environments) would then win.

## Problem

A process loads one version of each library, and plugins share typed classes, so one set of plugins means one environment. Installing each plugin as its own `uv tool` doesn't work: the environments are separate, and they conflict over the `untaped` executable (tested in an earlier session). So who builds the environment, and who keeps the list of what's in it?

## Prior art

| Model | Examples | Weakness |
|---|---|---|
| The environment decides | pytest, mkdocs, Sphinx, Airflow | retyping install lines |
| The tool installs into its own environment | `llm install`, `poetry self add`, `pdm self add` | `uv tool upgrade llm` wiped plugins; the fix keeps a second list |
| The project declares, the tool installs into a side folder | Poetry 2 `requires-plugins`, PDM `.pdm-plugins` | a second resolution outside the lock |
| A manifest is the truth | pixi global, Nix, ansible-builder, OTel `ocb`, xcaddy | works only when the manifest belongs to the builder |
| Each plugin its own program | `git-foo`, kubectl + krew, `gh` extensions, Terraform providers | no shared typed classes |
| A throwaway layer / frozen builds | `uvx --with`; PyApp, pex, shiv | |

The common failure (Poetry, llm) is **two lists of what's installed** drifting apart.

## Decision

- **untaped never keeps its own plugin list. The builder's record is the only list.**
- An **installer** words package, with one engine per builder. Each engine recognizes whether it built the current environment (uv's receipt, pipx metadata, …) and edits the list by calling the builder's own tool.
- `untaped plugin add jira` works when an engine recognizes the environment. Otherwise it states the facts ("add `untaped-jira` with the tool that built this environment").
- Bundles are the portable list; the builder's record is the local list. Each scope is one builder with one list: your machine (uv tool, pipx, pixi global), a project (pyproject + lock), one run (`uvx --with`), a runner (an image manifest), a frozen binary (its build manifest).

## Rejected

- **A pixi-style manifest owned by untaped.** It only works when you *are* the builder: the Poetry mistake.
- **untaped owning environments through a launcher.**

## Consequences

- Open risk, unverified: on Windows, uv *copies* tool executables, so adding a plugin to the tool you're running may need untaped to hand off and exit.
