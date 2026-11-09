# Glossary

**Architecture.** What the kernel contains: there out of necessity, mandatory for every plugin. See [architecture](architecture.md).

**Design.** Everything chosen on top of the kernel: conventions, packaging, first-party plugins. All of it can be swapped.

**Kernel.** `untaped-core`: how plugins find and call each other. A courier protocol built in Python. Standard library only.

**Host.** The program that starts untaped in a process and calls into the kernel: the CLI executable, an MCP server, a hub, an embedding program, a test. A role, not a bucket.

**Contract (extend).** An extension point where every provider answers and the asker decides what to do with the answers.

**Slot (replace).** An extension point exactly one engine fills. Its code name is a placeholder.

**Offers / fills.** What a plugin declares: the contracts and slots it owns, and those it answers or implements. The only declarations the kernel requires.

**Call, Reply, Result.** A call as a value; one receipt per provider, naming who answered (`PluginId`); `Ok(value)` or `Err(error)`.

**Dispatch.** The kernel's hook for the one carrier of every call. Empty → the kernel calls providers directly.

**Resolver.** The kernel's hook for choosing among engines and ordering providers. Several resolvers must agree.

**Loaded.** The kernel's moment "this plugin finished loading", with its declarations. Listeners only observe.

**Moment / question.** The two kinds of kernel hook: a moment announces what happened (observe-only); a question asks for an answer the kernel can't give without an opinion, and states how several answers combine.

**Scope.** A run's room: opened by a host (stdlib `contextvars`), in which slots can be bound to specific instances.

**Facts.** The kernel's outcomes other than a value: `LoadProblem`, `Unfilled`, `Undecided`, `Ambiguous`, `Loop`, `NoScope`, `Err`.

**Language.** A notation + words + grammar. untaped's notation is pydantic.

**Notation.** The library a language's words and grammar are written in; its validation is the language's validation. A property of a language, not a bucket.

**Words.** Rank 1: meaning only. One owner and a namespace per set; no implementation.

**Category.** The kind of a word, like a part of speech: Record (data that can be shown, piped or sent), Settings, errors and messages. Grammar is stated over categories.

**Value word.** A notation-annotated type with meaning (`GitUrl`, `Duration`). Not a category.

**Grammar.** Rank 2: how words connect. Sentence shapes (`Command`, a contract, a slot), roles, function words, bridges.

**Function word.** A word whose only meaning is a role in a sentence, like "of" or "from": `Output`, `Stdin`, `Confirm`.

**Parties.** Rank 3: the language in use, code that does things. Each package is a party to contracts and slots, holding roles; a third party is one that isn't the owner.

**Engine / provider.** Code that fills words. "Engine" usually means the one filler of a slot; "provider" one of the answerers of a contract.

**User.** Anything that depends on words.

**Role / bucket.** A bucket is what code is and fixes which way its dependencies point; a role is a relationship (fills, uses, opens scopes, checks). Roles combine freely.

**Bundle.** Rank 4: a package with only dependencies, used to curate an install. Invisible at run time.

**Mechanic.** A filler of a cross-cutting extension point that applies to everything it touches (`UserOrder`, `Cache`, `Table`).

**Builder.** The tool that builds the Python environment (uv tool, pipx, a project, an image builder). Its record is the only list of installed plugins.

**Question / action.** In the call grammar: a call that only asks (cacheable, retryable) or one that changes something (confirmation, dry runs). Not to be confused with a kernel question hook.

**Declined / stopped.** A user answering no to `Confirm` (command grammar) vs a run being cancelled (concurrency).
