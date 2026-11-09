# 0023. The words bucket

- **Status:** accepted · 2026-11-05 (afternoon) · the maintainer
- **Rests on:** [0022](0022-ranked-buckets.md) ranked buckets
- **Reopen if:** a word needs to carry a role that no sentence can wrap.

## Problem

What may a word (rank 1) contain? Today `Repo(Issued)` has `name: RepoName`, `url: GitUrl`, `default_branch`, `description`, `archived`, and, through `Issued`, a `source` field (which provider answered). Three kinds of link leave it, and they are not the same kind of thing.

## Decision

Words hold **meaning only**: one owner and a namespace (`git.*`) per set of words; no implementation.

- **Accepts "is a category."** A word declares its category, like a part of speech. Categories: **Record** (data that can be shown, piped or sent), **Settings** (what a user configures), **errors and messages**. Grammar is stated over categories. The categories sit at the bottom of the bucket; domain words rest on them.
  - ✓ `class Sprint(Record)` · ✗ `class JiraClient(Record)` (doing) · ✗ `class Repo(Settings)` (wrong category)
- **Accepts "has a" another word.** A definition may use other words, as a dictionary does.
  - ✓ `Repo.url: GitUrl` · ✓ `Sprint.start_at: UtcTimestamp`
- **Rejects "has a" engine.**
  - ✗ `Repo.store: RepoStore`
- **Rejects "plays a role."** A role belongs to the sentence, so it lives in grammar.
  - ✗ `class Repo(Issued)` storing `source`
  - ✓ the contract wraps answers: `Answer[Repo]` (repo + who answered); a rating contract's `Rated[Repo]` (repo + who rated + score)

## Rejected

- **"Words stand alone"** (the earlier wording): too strong. A word needs no grammar to exist, but its definition may use other words.
- **Roles inside words:** every contract involving repos would add fields to `Repo`, git's words would release each time, and third parties couldn't add their roles at all.

## Consequences

- `Issued` sits in `sdk/records.py` today "so an owner's model (kept in `state.yml`) loads without the contracts machinery". Moving the role to a wrapper changes what owners persist (an input for the code assessment).
- The `Answer[...]` wrapper itself was later replaced by the kernel's `Reply` ([0051](0051-reply-and-result.md)); the rule (roles are not in words) stands.

## How we got here

The maintainer asked whether `Repo` is grammar. The answer: `Repo` is a word, and one of its three links (`Issued`) is grammar in disguise. Lesson kept: strip doing before reading dependencies as meaning.
