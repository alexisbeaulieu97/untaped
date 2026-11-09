# Conventions: how to build a plugin that swaps well

> Draft. Names in code are placeholders.

These are rules we keep for ourselves so that "make your own" keeps working. The kernel doesn't enforce them, and a plugin that ignores them still loads and runs; it only gives up what they buy, mostly being swappable underneath whoever uses it. They get teeth from plugins that own a language (checking their own words on `Loaded`) and from our repository's tests ([0047](../decisions/0047-architecture-vs-design.md)).

We aim for conventions solid enough that people think twice before starting their own.

## The buckets

Everything sits in ranked buckets. Arrows mean "depends on / refers to": they point down, may skip ranks, may point sideways within a rank without cycles, and **never point up** (a relaxed layered system). Replacing a piece costs what rests on it, so the lower the rank, the costlier the swap ([0022](../decisions/0022-ranked-buckets.md)).

| Rank | Bucket | Holds | Swapping one costs |
|---|---|---|---|
| 0 | kernel | how plugins find and call each other (the [architecture](../architecture.md)) | a different ecosystem |
| 1 | words | meaning | everything referring to it needs bridges or new grammar |
| 2 | grammar | how words connect | a new language; the words stay |
| 3 | parties | the language in use: code that does things, each package a party to contracts and slots, holding roles (engine, user, host) ([0046](../decisions/0046-rank-3-parties.md)) | an engine: a choice by resolvers, users never notice; a user: nothing rests on it |
| 4 | bundles | compatible sets of packages | install a different set |

**A language = a notation + words + grammar.** untaped's notation is **pydantic**. Words and grammar are written in it, and its validation is the language's validation. The notation is a property of a language, not a bucket. A rival vocabulary may choose another notation; that makes it a new language ([0035](../decisions/0035-pydantic-is-the-notation.md)).

**Bucket vs role.** A bucket is what code *is*, and it fixes which way its dependencies may point. A role is a relationship (fills, uses, opens scopes, checks), and roles combine freely: one package may hold several ([0042](../decisions/0042-role-vs-bucket.md)).

Each bucket has a **definition** (what puts something in it), **rules** (only where a violation would go unnoticed or quietly break someone else) and **conventions** (where a check already reports the violation with facts) ([0034](../decisions/0034-bundles-definition.md)).

### Words (rank 1)

**Definition:** meaning only. One owner and a namespace (`git.*`) per set of words; no implementation ([0023](../decisions/0023-words-bucket.md)).

| | ✓ | ✗ |
|---|---|---|
| is a category | `class Sprint(Record)` | `class JiraClient(Record)` (doing); `class Repo(Settings)` (wrong category) |
| has a word | `Repo.url: GitUrl` | |
| has an engine | | `Repo.store: RepoStore` |
| plays a role | the contract wraps the answer | `Repo.source` (which provider answered) |
| presentation | an engine's own words, attached through `Annotated` | `-x`, default columns in untaped's words |

- Categories: **Record** (data that can be shown, piped or sent), **Settings** (what a user configures), **errors and messages**.
- **Value words** (`GitUrl`, `Duration`, `UtcTimestamp`) are notation-annotated types, not a category: `type GitUrl = Annotated[str, StringConstraints(...)]`. A value's human spelling (`"6h"`) is its owner's, through a validator.
- **Words never name or suggest an engine**, not even in metadata. A name in words is a weak reference: it goes stale or gets squatted. Bundles carry compatible sets. A slot nothing fills is reported as a named hole: which slot, which plugin needs it ([0014](../decisions/0014-bundles-words-never-name-engines.md)).
- An engine's hints (`Short("-x")`, `Positional`, `Column`) are that engine owner's own words package, attached through the notation's `Annotated` and ignored by engines that don't know them ([0036](../decisions/0036-engine-hints.md)).

### Grammar (rank 2)

**Definition:** meaning about how words connect, stated over categories ([0024](../decisions/0024-grammar-bucket.md)).

**Accepts**
- A1 rules over categories: ✓ `Output[R: Record]`; ✗ `Output[R: Sprint]`.
- A2 sentence shapes: ✓ `class List(Command)`, `class RepoSource(Contract)`, `repo_store = Slot(...)`; ✗ a plain function the CLI finds by name.
- A3 roles and function words (words whose only meaning is a role in a sentence, like "of"): ✓ `Output`, `Stdin`, `Confirm`, "declined". `Command` is grammar: it connects inputs, output, settings and the run.
- A4 bridges as promises: ✓ the `to_repo(item) -> Repo` signature; ✗ its body.

**Refuses:** R1 doing (✗ `@cached(6h)`; ✗ the CLI parts of today's `Output`: `--format`, `--columns`, renderers); R2 presentation; R3 new nouns (✗ `class Repo` defined beside `RepoSource`).

**Guarantees,** so any engine can act on any sentence without the author's help:
- G1 every sentence is declared and readable without running it; the notation's JSON Schema is that readable form. ✗ `**kwargs -> Any`; ✗ methods found by `isinstance`.
- G2 one call point: ✓ `ask(RepoSource).repos()`; ✗ `GitHubSource().repos()`.
- G3 only what the notation can convert crosses, checked by the call grammar on `Loaded` with a `TypeAdapter` per parameter and answer. ✗ an `httpx.Client` or a `Callable`. Formats (JSON, YAML, the CLI's text, a hub's binary) are each engine's choice.
- G4 policy is a setting, not code: ✗ `@cached(6h)`, `retries=3`, `parallel=4` in a signature.
- G5 secrets are marked through the notation (`SecretStr`, `Secret[T]`): ✗ `token: str`.
- G6 every call declares whether it is a **question** or an **action**. Engines cache and retry only questions; actions get confirmation and dry runs ([0031](../decisions/0031-call-grammar.md)).

### Parties (rank 3)

**Definition:** doing. An engine fills words; a user depends on them ([0025](../decisions/0025-parties-bucket.md)).

**Accepts**
- E1 filling a slot. A package that offers a slot never ships its filler (`untaped-git` offers `repo_store`; `untaped-git-cli` fills it).
- E2 answering a contract: ✓ `class GitHubRepos(RepoSource)`; ✗ an importable `list_repos()`.
- E3 using words and grammar: ✓ yield `Sprint`s; ✗ `print`.
- E4 its own settings, commands and doctor checks (`git-cli`'s `store_dir`, not in git's words).
- E5 words that only make sense for this engine (`StoreReport` in git-cli; ✗ `Repo` in git-cli).
- E6 acting on sentences from outside: ✓ a cache around calls; ✗ `lru_cache` inside a provider.

**Refuses:** X1 reaching into another plugin's internals; X2 adding to grammar it doesn't own (✗ `isinstance(src, CliRepoSource)`; ✓ declare its own contract); X3 policy as constants (✗ `PARALLEL = 4`).

**Allowed, but visible:** V1 a user depending on a specific engine (declared, and it gives up the swap). **Within:** W1 one package may hold several roles.

### Bundles (rank 4)

**Definition:** a package with only dependencies and no entry points. One with an entry point is a plugin. Invisible at run time; it chooses by including ([0034](../decisions/0034-bundles-definition.md)).

**Conventions** (each violation is already reported elsewhere): floors only (pins → the installer refuses and names the conflict); works out of the box, tested at the lowest and newest versions with all extras (otherwise the kernel reports `Unfilled`/`Undecided`/`Ambiguous`); ships no settings or picks (nothing would read them). Extras are allowed. Anyone can publish a bundle. A plugin may depend on a bundle; it is visible, and doctor reports engines chosen for nothing as idle.

## The mechanics convention

**Domain** extension points (`RepoSource`, `repo_store`) are filled case by case: that is their purpose. **Cross-cutting** extension points (`Resolver`, renderers, settings sources, call wrappers) are filled only by **mechanics** that apply to everything they touch (`UserOrder`, `Cache`, `Table`). A cross-cutting filler that names one particular slot, contract or record (`RepoSourceOrder(Resolver)`, `SprintTable(Renderer)`) is a smell: it leads to a package explosion and turns plugins into "one package with extra steps" ([0033](../decisions/0033-mechanics-convention.md)).

## Words describe meaning, never presentation

| Word | Meaning | CLI engine | MCP engine |
|---|---|---|---|
| a field `name: str` | a required input | `--name` | a tool parameter |
| `Output[Repo]` | a list of repos | table / json / pipe | structured JSON |
| error category `not_found` | failed: not found | exit 1 + stderr | a tool error |
| `Confirm` | needs consent | `--yes` or a prompt | a confirm parameter or the client's approval |
| docstring | what it does | `--help` | the tool description |

([0007](../decisions/0007-meaning-never-presentation.md))

## The run rule

Nothing a plugin reaches during a run comes from the process. Everything per run is reached through a slot resolved in the scope the host opened, never a module global ([0008](../decisions/0008-the-run-rule.md), [0038](../decisions/0038-settings-and-no-run-family.md)). On a hub, anything read from the process (environment, working directory) would leak one user's state into another's run. See [hosts](hosts.md).

## Unattended

Every question a command asks is a request that a front end answers (prompt, dialog, form), or that a flag, environment variable or configuration value answers ahead of time ([0009](../decisions/0009-unattended.md)). This is what makes front ends swappable.

## Placement: where a new thing goes

1. **Capability or detail?** Could a different implementation reasonably exist, or could someone build on it? Detail → private. Capability → publish it, `@experimental` until its shape is proven. Decided by intent, never by today's demand ([0011](../decisions/0011-placement-by-intent.md)).
2. **Meaning or doing?** Meaning → the kernel only if the kernel reads it; else an existing owner's words or grammar; else a new package. Doing → a plugin if it fills words, must be swappable or configurable, or touches the run; else a plain library (`plural`, `ago_text`), usable from any rank.
3. Ownership comes before packaging; then the [package-line rule](packaging.md).

## Typed references

Refer to things by objects, not strings: `Ambiguous(slot=repo_store)`, not `"git.repo_store"`. Strings appear only where users type them (config keys, environment variables, entry-point names) or at packaging and wire boundaries (`PluginId`).

## Other rules we keep

- Users depend on words; depending on an engine is visible and gives up the swap ([0002](../decisions/0002-words-engines-users.md)).
- Anyone may publish words; each set has one owner; disagreement is handled by competing words plus bridges ([0004](../decisions/0004-words-without-a-center.md)).
- First-party plugins get no privileges: they install, version and plug in like third-party ones.
- No guards that only look like protection: a plugin runs with the user's rights. Checks catch honest mistakes; they don't wall plugins off.
- untaped handles only data that crosses its boundaries (command inputs, settings, records, errors and messages). A plugin's own data is the plugin's business ([0020](../decisions/0020-core-is-the-kernel-only.md)).

## Named patterns

The whole is POSA's **Microkernel**. Resolvers, renderers, settings sources and `Dispatch` fillers are **Strategies**; call wrappers are **Decorators**; words and grammar are **ports**, engines **adapters**. `ask(...)` has the shape of a **Service Locator**, chosen deliberately: a missing filler is reported as a fact at the call (`Unfilled`), and a plugin may own a `uses` declaration to check it earlier ([0033](../decisions/0033-mechanics-convention.md), [0048](../decisions/0048-kernel-requirement-rule.md)).
