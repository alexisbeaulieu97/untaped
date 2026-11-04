**Breaking (users):** with no source selected, `untaped ansible deps`, `find`
and `graph` read each repo live from the plugin that lists it (a plugin
filling git's `RepoSource`) instead of from GitHub's API: the repo is fetched
into the repo store, so a later read fetches only refs that moved. A
dependency no plugin lists, whose ref is no branch or tag (a bare commit), or
that the credential can't clone warns and stays unread; a target that can't
be read fails the command, as `find` does when none of its roots can be. A
URL in a requirements file names a repo when its host is one a refreshed
source's repos live on or one an installed plugin serves (`untaped git
hosts`), no longer `github.server.url`'s by itself.
**Breaking (users):** `untaped-ansible` no longer installs `untaped-github`:
install a plugin that lists repos beside it, such as `untaped[github]`.
