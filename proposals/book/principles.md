# Principles: how we design. One entry per line; "against:" and "fine:" lines under an entry are its examples. A violation must be visible; an exception can be justified.

A plugin refers to other plugins only through their public interfaces.
A capability that two plugins we maintain both need has one owner.
Code that plugins share is a plain library only when it does nothing but compute a result from its arguments.
against: github and gitlab both import `http_session()` from `untaped_common`, a plain package that opens their network connections.
against: github and gitlab both import `cached()` from a plain package that keeps answers in a file between runs.
against: github and gitlab both import `page_size()` from a plain package that reads it from the user's configuration.
fine: github and gitlab both import `plural()` from a plain package to word their messages.
A plugin's public interface is made only of [term]s and [contract]s.
A public interface asks for and returns only what every implementation of it could supply.
against: git's credential contract takes `helper: GitCredentialHelper`, so a credential provider must speak git's helper protocol to answer.
against: git's `RepoSource` contract returns a required `gh_node_id` for each repo, which only GitHub has.
fine: git's credential contract takes `host: str` and returns a `Secret` term, which a keychain, a vault or git's own helper can all supply.
A plugin uses a capability only through what every implementation of it shares.
against: The `sync` operation takes github's own `GitHubRepo` term, which adds `fork_parent` to git's `Repo`.
fine: The github plugin's own `gh-stats` operation takes github's `GitHubRepo` term.
A part of untaped depends on a [contract], never on which plugin fills it.
against: The CLI host refuses to start unless the `untaped-pydantic` plugin is installed, even when another plugin fills the parse contract.
fine: The CLI host asks for whatever plugin fills the parse contract; pydantic's or msgspec's both serve.
Plugins refer to each other's interfaces by type, never by name.
No part of a [term] or of a [contract] is left untyped.
A plugin acts only on what it is given.
against: The `clone` operation prompts for a missing token only when `sys.stdin.isatty()` is true, and fails otherwise.
against: The GitHub provider returns fewer fields when `current_host() == "mcp"`, to keep agents' context small.
fine: The `audit` operation shows a repo's visibility only to callers allowed to see it; the caller's rights come in as an input, `caller: Rights`.
A plugin declares nothing about anything but itself and what it depends on.
against: The GitHub provider declares `used_by = (Sync, Report)`, naming the operations that call it.
fine: Add a field `archived: bool | None` to git's `Repo` term: whether the repository is archived on its forge, None when it is on none.
A [term] holds only facts about the thing it describes.
against: Add a field `fetched_at: UtcTimestamp` to git's `Repo` term: when the provider fetched it.
fine: Add a field `stars: int | None` to git's `Repo` term: how many stars the repository has on its forge, None when it is on none.
A field of a [term] is optional only when the thing it describes can lack it.
against: Make every field of git's `Repo` term optional, so providers that know less can still return one.
fine: Make `remote_url` on git's `Repo` term `GitUrl | None`, since a repository that exists only on disk has no remote.
[term]s and [contract]s say nothing about what is shown, how it is shown, or how a value is entered.
against: The `List` operation marks its `board_id` input with `Short("-b")` from the CLI's terms.
against: Mark the `url` field of git's `Repo` term with `Column(width=40)`, so the CLI's table shows it at a readable width.
against: The `clone` operation marks its `token` input with `PromptIfMissing`, so a terminal asks for it when it is not given.
fine: The `List` operation's docstring says which repositories it lists.
What an [operation]'s [contract] says about it states facts, never what someone should do about them.
against: The `delete` operation's contract marks it with `Confirm`, so whoever runs it is asked for consent first.
against: The `deploy` operation's contract marks it `HumanOnly`, so agents do not run it.
fine: The `delete` operation's contract marks it `Irreversible`: what it removes cannot be restored.
fine: git's `RepoSource.repos()` is declared read-only: it changes nothing.
fine: The agent plugin's settings let a user keep agents away from operations marked `Irreversible`.
[term]s and [contract]s never depend on code that does the work.
against: git's credential contract returns the token as a `pydantic.SecretStr`.
against: git's `Repo` term types `name` as `Annotated[str, annotated_types.Predicate(is_valid_slug)]`.
fine: git's `Repo` term types `stars` as `Annotated[int, annotated_types.Ge(0)]`.
fine: Add a field `pushed_at: datetime | None` to git's `Repo` term: when the repository was last pushed to, None when never.
untaped gives a plugin we maintain nothing that another plugin could not have.
against: The kernel skips the signature check for plugins whose manifest sets `first_party = true`.
against: When two plugins fill the same slot, the resolver prefers the one from the untaped repository.
against: Installing the package that provides untaped's kernel also installs our github plugin.
fine: A bundle of our plugins, installed only when someone asks for it, published like any other bundle.
The kernel holds only what it needs to load plugins, route calls between them, and let plugins act on both.
against: The kernel defines a `GitUrl` term so that the git, github and gitlab plugins all share one.
against: The kernel provides `retry()` for plugins to wrap their network calls in.
fine: The kernel finds the plugin that fills the slot an operation asks for and passes the call to it.
fine: The kernel tells every plugin that handles `CallFinished` when a call it routed has finished.
The kernel decides nothing that a plugin could decide.
against: When two plugins fill the same slot, the kernel picks the one installed first.
fine: When two plugins fill the same slot, the kernel reports `Ambiguous` and asks the plugins that fill its resolver extension.
Only the [user] limits what a way of driving untaped reaches.
against: The MCP host exposes only the operations listed in its own `EXPOSED = (List, Clone)`.
against: Only the CLI can filter `list` by owner; the MCP host returns everything.
fine: The agent plugin's settings let a user keep agents away from operations marked `Irreversible`.
fine: The CLI shows the result of `list` as a table, and the MCP host returns it as JSON.
fine: The user installs a schema plugin, and from then on every host refuses calls whose inputs break their terms' constraints.
