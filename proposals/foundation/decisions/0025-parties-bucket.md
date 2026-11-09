# 0025. The parties bucket (E, X, V, W)

- **Status:** accepted · 2026-11-05 (afternoon) · the maintainer
- **Rests on:** [0022](0022-ranked-buckets.md) ranked buckets · [0024](0024-grammar-bucket.md) the grammar bucket
- **Note:** settled as the "engines and users" bucket; the rank was renamed "parties" in [0046](0046-rank-3-parties.md).

## Problem

Rank 3 is doing: code that acts on words. Engines fill words; users depend on them. What may such a package do, and what must it not do, so swaps keep working?

## Decision

These are **roles**, not kinds of package: one package may hold several.

**Accepts:**
- **E1** filling a slot, never shipped by the package that offers it: ✓ `untaped-git-cli` fills `repo_store`
- **E2** answering a contract: ✓ `class GitHubRepos(RepoSource)` · ✗ an importable `list_repos()`
- **E3** using words and grammar: ✓ yield `Sprint`s · ✗ `print`
- **E4** its own settings, commands and doctor checks: ✓ git-cli's `store_dir`, not in git's words
- **E5** words that only make sense for this engine: ✓ `StoreReport` in git-cli · ✗ `Repo` in git-cli
- **E6** acting on sentences from outside: ✓ a cache wrapper around calls · ✗ `lru_cache` inside a provider

**Refuses:**
- **X1** reaching into another plugin's internals (awx imports ansible's parser and `repo_key` today)
- **X2** adding to grammar it doesn't own: ✗ `isinstance(src, CliRepoSource)` · ✓ declare its own contract
- **X3** policy as constants: ✗ `PARALLEL = 4`

**Allowed, but visible:**
- **V1** a user depending on a specific engine: declared, and it gives up the swap.

**Within:**
- **W1** one package may hold several roles (git-cli fills a slot and has its own commands).

## Consequences

- E6 works only because of G2, G4 and G6 ([0024](0024-grammar-bucket.md)).
- X1 and V1 differ in whether the dependency is declared and goes through the engine's public interface.
- V1 later also covered a plugin depending on a bundle that contains an engine ([0034](0034-bundles-definition.md)).
