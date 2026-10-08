Repo caches (workspace, ansible, github) work from agent shells that set
git's `safe.bareRepository=explicit`, such as GitHub Copilot CLI, instead of
failing with "cannot use bare repository".
([#534](https://github.com/alexisbeaulieu97/untaped/pull/534))
