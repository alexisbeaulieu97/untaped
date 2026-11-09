# 0024. The grammar bucket (A, R, G1–G6)

- **Status:** amended · 2026-11-05 (afternoon) · the maintainer
- **Rests on:** [0022](0022-ranked-buckets.md) ranked buckets · [0023](0023-words-bucket.md) the words bucket
- **Amended by:** [0035](0035-pydantic-is-the-notation.md) G3 reworded to "only what the notation (pydantic) can convert crosses" · [0049](0049-kernel-hooks-loaded.md) G3 is checked by the call grammar on the `Loaded` moment, not through `OwnerCheck`
- **Reopen if:** an engine can't act on a sentence without its author's help despite G1–G6.

## Problem

Grammar is meaning about how words connect. The maintainer's frame: "words → meaning, grammar → connect words, engine → act on them." Their requirement: grammar must be **composable enough that engines can easily act on it**, e.g. caching applied by an engine instead of `@cached` written into the contract.

## Decision

**Accepts:**
- **A1** rules stated over categories: ✓ `Output[R: Record]` · ✗ untaped defining `Output[R: Sprint]`
- **A2** sentence shapes: ✓ `class List(Command)`, `class RepoSource(Contract)`, a `Slot` · ✗ a plain function the CLI finds by name
- **A3** roles and **function words** (words whose only meaning is a role in a sentence, like "of" or "from"): ✓ `Output`, `Stdin`, `Confirm`, "declined" · ✗ a `Repo.source` field. `Command` itself is grammar.
- **A4** bridges as promises: ✓ the `to_repo(item) -> Repo` signature · ✗ its body

**Refuses:**
- **R1** doing: ✗ `@cached(6h)`; ✗ the CLI parts of today's `Output` (`--format`, `--columns`, renderers, offered formats, the empty-result message)
- **R2** presentation: ✗ `--format`, `Short("-x")`
- **R3** new nouns: ✗ `class Repo` defined beside `RepoSource`

**Guarantees, so engines can act on any sentence without the author's help:**
- **G1** every sentence is declared and readable without running it: ✗ `**kwargs -> Any` · ✗ methods found by `isinstance`
- **G2** one call point: ✓ `ask(RepoSource).repos()` · ✗ `GitHubSource().repos()`
- **G3** only categories cross (✗ an `httpx.Client`, ✗ a `Callable`); this first wording was reworded to "only plain data crosses", then settled as "only what the notation can convert crosses" in 0035
- **G4** policy is a setting, not code: ✓ `git.extensions.repo_source.max_age` · ✗ `@cached(6h)`, `retries=3`
- **G5** secrets are marked: ✓ `password: Secret[str]` · ✗ `token: str`
- **G6** every call declares whether it is a **question** or an **action**: engines cache and retry only questions; actions get confirmation and dry runs

## Rejected

- Four earlier guarantees (declared; one wrappable call point; "only categories cross, so everything can be turned into JSON"; policy is a setting): replaced by G1–G6. "Turned into JSON" came from today's code; a wire format is an engine's choice.

## Consequences

- The test against today's code found the gaps G5 and G6 close: secrets crossing in `GitHost.credential`, and `StepKind.run` changing the machine while `RepoSource.repos` only asks.
- `Output` and `Stdin`, stripped of CLI work, mean "this command yields R" and "rows arrive over time"; they are function words owned by the grammar that uses them.
- E6 (engines acting on sentences from outside, [0025](0025-parties-bucket.md)) works only because of G2, G4 and G6.
