# Goals: why untaped exists. One entry per line; "against:" and "fine:" lines under an entry are its examples.

Anyone can install any plugin or write their own, including a replacement for a first-party one.
Replacing a plugin with another that has the same public interface needs no change to what was built on it.
against: The `sync` operation imports `GitHubRepos` from the github plugin and calls it directly.
fine: Add a field `archived: bool | None` to git's `Repo` term: whether the repository is archived on its forge, None when it is on none.
No way of driving untaped is privileged over another.
Everything untaped does can run unattended: whatever an [operation] asks can be answered in advance, or another [operation] does the job without asking.
Nothing in the kernel ties it to untaped's own plugins, so a separate project can build on it.
