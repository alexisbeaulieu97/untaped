# Settings

> Draft. How settings sources are ordered (defaults, user, project, ...) is under question ([0045](../decisions/0045-two-kinds-of-order.md) is reopened). It is this plugin's own design unless it turns out to need the kernel.

Settings are words, a slot and an engine, like any other capability ([0038](../decisions/0038-settings-and-no-run-family.md)).

- **Discovered from declarations.** A class with settings declares their model (`Configured[GitHubSettings]`, a command's settings). The settings owner gathers them on `Loaded`, without running anything, so doctor can validate every section before a run.
- **Delivered through a slot.** The settings words offer `settings_store`; the settings engine fills it (reads files, the environment through the `process` slot, the project) and validates through the notation. `Configured.settings` asks it.
- **Per user** through the scope: a hub binds each user's own `settings_store` in that user's scope.

```python
class GitHubRepos(RepoSource, Configured[GitHubSettings]):
    def repos(self) -> Iterable[Repo]:
        token = self.settings.token        # asks settings_store in the current scope
```

github, the settings engine and a hub each stand alone: none needs the others to be written.
