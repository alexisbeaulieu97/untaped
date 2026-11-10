# Changelog

## 12.0.0

### Upgrading

- A plugin imports `untaped.sdk.contracts` instead of `untaped.contracts` and
  `untaped.sdk.testing` instead of `untaped.testing`, and its
  `tests/conftest.py` names `untaped.sdk.testing.plugin`. A name that moved to
  a group is imported from it (`from untaped.sdk.storage import Cache`,
  `from untaped.sdk.network import HttpClient`); an import of it from
  `untaped.sdk` fails with `ImportError`. A screen imports from
  `untaped.screen.core` and `untaped.screen.components`.
  The names that were core's own (`read_stdin`, `table_row`, `HttpSettings`,
  `connected_client`, `Written`, `HolderState`) have no SDK spelling: a
  plugin reads its input through the `Stdin` and `Lines` fields and reaches a
  server through `HttpClient`.
  ([#836](https://github.com/alexisbeaulieu97/untaped/pull/836))
- In `config.yml`, rename `http.ca_bundle` to `untaped.http.ca_bundle_path`, and
  `UNTAPED_HTTP__CA_BUNDLE` to `UNTAPED_UNTAPED__HTTP__CA_BUNDLE_PATH`.
  ([#837](https://github.com/alexisbeaulieu97/untaped/pull/837))
- A settings field that relied on `# untaped: allow settings-naming` is
  renamed to follow the naming rules, declaring its old name as the
  conventions reference's "Renaming a setting" describes, so
  `untaped config migrate` moves users' values.
  ([#837](https://github.com/alexisbeaulieu97/untaped/pull/837))
- A plugin test that read the `# untaped: allow` marker or resolved imports
  through `untaped.sdk.testing` checks the source with `untaped lint` instead.
  ([#837](https://github.com/alexisbeaulieu97/untaped/pull/837))
- A plugin that reads `untaped plugin list` records imports
  `untaped.sdk.InstalledPlugin` instead of `untaped.sdk.Plugin`, and a screen
  matches the key press message as `untaped.screen.core.KeyPress`.
  ([#839](https://github.com/alexisbeaulieu97/untaped/pull/839))
- Pass a `DoctorCheck`'s `title` and `run` by keyword (`api =
  DoctorCheck(title="acme API", run=probe)` on the plugin class), and build a
  changed copy of one, or of a `DeprecatedBehaviour`, with a new call instead
  of `dataclasses.replace`.
  ([#843](https://github.com/alexisbeaulieu97/untaped/pull/843))
- A script that runs `untaped plugin list --contracts` runs `untaped plugin
  contracts`, and one that reads `untaped.plugin` or `untaped.contract` records
  reads `untaped.installed_plugin` and `untaped.contract_method`. One that runs
  `untaped plugin check` runs `untaped lint`.
  ([#860](https://github.com/alexisbeaulieu97/untaped/pull/860))
- Replace `UNTAPED_CONFIG=/some/dir/config.yml` with `UNTAPED_HOME=/some/dir`. A
  config file with another name moves to `config.yml` in its own folder; log in
  again there (`untaped auth login <plugin>`), since stored logins are keyed by
  the config file's path. `UNTAPED_STATE` has no replacement: each plugin's
  state lives in its folder under the home. A plugin's tests write `home.path /
  "x"` where they wrote `home.path / ".untaped" / "x"`.
  ([#862](https://github.com/alexisbeaulieu97/untaped/pull/862))
- In each profile of `config.yml`, move `http:`, `ui:` and `skills:` under an
  `untaped:` key (`untaped.http.proxy` in the base), and rename
  `UNTAPED_HTTP__X` to `UNTAPED_UNTAPED__HTTP__X` (likewise `UI` and `SKILLS`).
  A plugin reads them with `ctx.http` and `ctx.terminal`.
  ([#863](https://github.com/alexisbeaulieu97/untaped/pull/863))
- Log in again with `untaped auth login <plugin>` for awx and jira: a token in
  `awx.token`, `awx.token_command`, `jira.token` or `jira.token_command` is no
  longer read. Replace `untaped.http.verify_ssl: false` with `verify_tls: false`
  on each server that needs it.
  ([#864](https://github.com/alexisbeaulieu97/untaped/pull/864))
- Start `config.yml` again for 12.0 (`untaped setup` writes a format 2 file),
  or rewrite it by hand: add `format_version: 2` at the top, move the
  `profiles.default` sections to the top level, keep the other profiles under
  `profiles:`, and replace `active: NAME` with `export UNTAPED_PROFILE=NAME` in
  your shell. ([#865](https://github.com/alexisbeaulieu97/untaped/pull/865))
- Scripts that read `profile list`'s `active` column or `profile show`'s
  `active` key read `selected`; scripts that read `profile current --format
  json` read `profiles[].source` instead of a single `source`. A script that set
  `UNTAPED_PROFILE` to make `config set` write into a profile now passes
  `--profile NAME`. Rename any profile whose name has spaces, commas or a
  leading `.` or `-`.
  ([#866](https://github.com/alexisbeaulieu97/untaped/pull/866))
- Scripts that read `profile show` read the settings under `data` (`yq .data`
  or `jq .data`); the `# profile:` stderr line is gone, so read `name` and
  `selected` from the record. A script that runs `profile delete` without a
  terminal passes `--yes`, as before.
  ([#867](https://github.com/alexisbeaulieu97/untaped/pull/867))
- A script that passes a credential's key field to `auth login` or `auth
  forget` as `--vault-id prod` passes `--key vault-id=prod`.
  A script that runs `auth forget` without a terminal still
  passes `--yes`; the error without it now names the question it would ask.
  ([#869](https://github.com/alexisbeaulieu97/untaped/pull/869))
- A plugin that passes `SkillAsset(source=...)` drops `source`; the facts its
  files held go on its pages. A copy an 11.x untaped installed is left where it
  is as a hand-made skill: delete it, then run `untaped skills install`. A
  script that reads `kind` or `source` from `skills list` or `skills status`
  stops reading them.
  ([#872](https://github.com/alexisbeaulieu97/untaped/pull/872))
- A script reading `untaped doctor -f json` by `check` reads core's config
  rows as `untaped.config-permissions`, `untaped.unknown-keys` and
  `untaped.token-store`.
  ([#876](https://github.com/alexisbeaulieu97/untaped/pull/876))
- Run `untaped setup register` again from each install you keep, and
  `untaped project init` again in each project: nothing reads
  `~/.untaped/refs/` any more, so it can be deleted.
  ([#878](https://github.com/alexisbeaulieu97/untaped/pull/878))
- A plugin that brought old repositories into the repo store with
  `untaped_git.api.adopt` has no replacement: 12.0 starts from a clean home, so
  a plugin clones into the store afresh.
  ([#880](https://github.com/alexisbeaulieu97/untaped/pull/880))
- From 11.0, refresh each ansible source once after upgrading (`untaped ansible
  source refresh NAME`), then delete `~/.untaped/plugins/ansible/index.sqlite3`:
  12.0 builds the index anew rather than adopt 11.0's file.
  ([#881](https://github.com/alexisbeaulieu97/untaped/pull/881))
- Run `untaped setup migrate-dirs` with 11.1 before upgrading if an older
  untaped's folders are still in the home: 12.0 does not move them. For a store
  layout doctor's `stores` row names as kept, follow the reason the row gives.
  ([#881](https://github.com/alexisbeaulieu97/untaped/pull/881))
- A plugin drops its `migrations=`; `dir_bytes` stays in `untaped.sdk`.
  ([#881](https://github.com/alexisbeaulieu97/untaped/pull/881))
- A script or plugin that wants only the plugins installed into untaped from
  `untaped plugin list --format json`, `installed_plugins()` or `ctx.plugins`
  skips the row whose `root` is true.
  ([#899](https://github.com/alexisbeaulieu97/untaped/pull/899))
- In a provider, replace `Configured[S]` with `Provider[S]` (imported from
  `untaped.sdk.contracts`), and subclass plain `Provider` where a provider read
  no settings. ([#907](https://github.com/alexisbeaulieu97/untaped/pull/907))
- In a contract, remove `@bridge` and `@listing` (and their imports), and
  replace `@cached(CACHE)` with `@cached(max_age=..., label=...)` taking the
  cache's max age and label; delete the `Cache` and the plugin class attribute
  that held it, and spell a command's `Refresh(CACHE)` as
  `Refresh(Contract.method)`. A method that returned a list without `@listing`
  now drops an invalid row instead of failing the whole answer.
  ([#908](https://github.com/alexisbeaulieu97/untaped/pull/908))
- A script that pipes a comment into `untaped jira issues comment KEY` adds
  `--body -`. One that sets `jira.base_url` sets `jira.server.url`. One that
  passes `--yes` to `jira issues create`, `jira issues comment` or `jira issues
  links create` drops it. One that set `jira.confirm: never` passes `--yes` to
  `jira issues patch` and `jira issues transition`.
  ([#914](https://github.com/alexisbeaulieu97/untaped/pull/914))
- In code that asks a contract, import `Errored` for `Failed`, use
  `answers.ok`/`.errored`/`.skipped` for `isinstance` loops, compare reasons
  with `Reason` members, read `NotReady.spelled` for the full key, and drop
  `needs=` and `deadline=`. In a provider, return
  `NotReady("why", key(MySettings).field)` for `setting="plugin.field"` and
  replace `self.http` with a client from `self.ctx.http.client(self.settings,
  ...)`. ([#915](https://github.com/alexisbeaulieu97/untaped/pull/915))
- Drop `kind=` from a contract's owner model and a provider's own record (the
  kind becomes the class name, so rename a class whose kind should stay). A
  12.0 command reading piped rows declares `stdin: Stdin[Owner] =
  Stdin(via=Contract.bridge)` and calls `self.stdin.values(given)` in place of
  `convert(Contract.bridge, envelope)` over `PipeEnvelope`s. A fake given to
  `install(Owner, providers=...)` is named by the providers' key.
  ([#917](https://github.com/alexisbeaulieu97/untaped/pull/917))
- Delete a provider's committed `fills.json`; nothing reads it. A script
  reading `owner-schema-drift` or the `fills`/`schema` rows from `doctor -f
  json`, `plugin check` or `untaped lint` drops them.
  ([#918](https://github.com/alexisbeaulieu97/untaped/pull/918))
- Replace `providing(owner, {"house": [HouseRules()]})` with
  `providing({"house": (HouseRules,)})`, and make each class a `Provider`
  (`class HouseRules(Rules)` already is one: lint's `Rules` is a `Provider`).
  ([#918](https://github.com/alexisbeaulieu97/untaped/pull/918))
- A dotfiles repo's `dotfiles.yml` becomes one file per package under
  `.untaped/dotfiles/`, each starting `version: 1`, with `source:` spelled
  `path:` (the dotfiles README shows one). A machine subscribes and adds its
  packages again under 12.0, which starts from a clean home. `dotfiles sync`
  is gone: `dotfiles apply --yes` places every package that is on, whatever
  its policy, so a timer that only reports runs `dotfiles status --check`. A
  prompt that read `dotfiles summary` reads `status.json`. A script that
  matched the `dotfiles.item`, `dotfiles.path_status` or
  `dotfiles.apply_outcome` rows reads `dotfiles.package`,
  `dotfiles.entry_status` and the new `dotfiles.apply_outcome` keys
  (`source`, `package`, `item`, `entry`).
  ([#920](https://github.com/alexisbeaulieu97/untaped/pull/920))
- A script that matches the `git.store` kind matches `git.store_report`. A
  worktree whose credential helper line has no `--nonce` gets one when
  untaped next writes its config: a workspace `fetch` or `configure`, or the
  workspace created again under 12.0, which starts from a clean home.
  ([#922](https://github.com/alexisbeaulieu97/untaped/pull/922))
- A plugin's doctor body that builds `DoctorResult` positionally passes
  `ok` first: `DoctorResult(True, "detail")`, or drops `id=` and names the
  rest. One passing `id=` keeps working.
  ([#925](https://github.com/alexisbeaulieu97/untaped/pull/925))
- A plugin that calls `RepoStore.write_worktree_config(tree, profile=...)`
  drops the `profile` keyword: `write_worktree_config(tree)`.
  ([#928](https://github.com/alexisbeaulieu97/untaped/pull/928))
- A script that matches the `workspace.status` kind matches
  `workspace.repo_status`. A worktree's git uses the profile the folder's
  project picks: `untaped --profile NAME workspace create ...` records it, and
  `UNTAPED_PROFILE` in the shell still stacks on it. A script that ran `cd
  "$(untaped -q workspace create NAME ...)"` creates first, then runs `cd
  "$(untaped workspace path NAME)"`.
  ([#928](https://github.com/alexisbeaulieu97/untaped/pull/928))
- A script that ran `github sweep --show files` runs `github sweep files`
  (`matches` likewise); one that passed `--no-fork` passes `--fork exclude`,
  and a bare `--fork` becomes `--fork only`. Store the GitHub token with
  `untaped auth login github`, or keep it in `GH_TOKEN`; point GitHub
  Enterprise at `github.server.url`, and set a sweep's cache age with
  `github.caches.sweep.max_age_seconds`. A script reading `fetched_at` from a
  `cache status` or `sweep` row parses an ISO 8601 timestamp.
  ([#930](https://github.com/alexisbeaulieu97/untaped/pull/930))
- A script that ran `ansible impact ROLE --uses NAME` runs `ansible impact uses
  ROLE NAME` (each `--uses` value becomes one more NAME). Where you set
  `ansible.index_path`, set `ansible.index_dir` to the folder that held it;
  `source refresh` builds the index there (12.0 never reads 11.x's file). Set
  the cache age with `ansible.caches.source_data.max_age_seconds`. Saved sources
  and aliases start empty in a 12.0 home: save them again with `source set` and
  `source-alias set`, then `source refresh`. A script reading `source-alias
  remove` rows finds no `repo` on the row of a damaged alias.
  ([#932](https://github.com/alexisbeaulieu97/untaped/pull/932))
- A plugin whose table showed a `*_s` or `elapsed` field as a run time marks it
  `Annotated[float, Duration]`, and one whose `sha`, `commit`, `revision` or
  `scm_revision` column was shortened marks it `Annotated[str, ObjectName]`; a
  command that emits plain dicts emits a `Record` with those fields to keep the
  formatting. ([#934](https://github.com/alexisbeaulieu97/untaped/pull/934))
- Run `untaped docs` where you ran `untaped agents`: `untaped agents PLUGIN
  COMMAND` is `untaped docs PLUGIN commands COMMAND`, and `untaped agents
  PLUGIN GUIDE` is `untaped docs PLUGIN guides GUIDE`. Stub skills in your
  home folder refresh on the first 12.0 command; run `untaped skills update`
  in a project that keeps its own.
  ([#936](https://github.com/alexisbeaulieu97/untaped/pull/936))
- A plugin whose table coloured a status word of its own (`synced`, `ok`,
  `stale`) marks the field with `Status(success=...)`, `warning=` or `error=`
  for those words; core's outcome and check words need nothing. A command
  that prints a view of a record as a `dict` builds each row with
  `table_row(record, row)`, so the row keeps its record's colours.
  ([#937](https://github.com/alexisbeaulieu97/untaped/pull/937))
- With `UNTAPED_HOME` set, move the library from
  `~/.untaped/plugins/recipe/library` to `plugins/recipe/library/` under that
  home, or set `recipe.library_dir` to where it is. A script that parsed
  `hooks run` JSON for a validate reads the row's `status` and `message`, as
  before, and a `fail` also has `error`; one that read the table of a
  validate reads `STATUS: MESSAGE` lines, or passes `-f json`. Of the guides,
  `trust` is now `guides hooks`, `writing-hooks` is `guides hook-contract`,
  and `recipe-files` is `untaped docs recipe inputs recipe_file`.
  ([#939](https://github.com/alexisbeaulieu97/untaped/pull/939))
- A plugin filling `GitHost` drops its `proxy` method; untaped's proxy covers
  its host's HTTPS fetches. Set `untaped.http.proxy` only where every HTTPS
  Git host should go through it, or leave it unset and let Git's own config
  choose per host.
  ([#941](https://github.com/alexisbeaulieu97/untaped/pull/941))
- Of lint's guides, `writing-rules` is now `guides rule-module` and `lint-file`
  is `untaped docs lint inputs lint_file`. A script that parsed a lint table
  for repeated blank targets reads every row's target, or passes `-f json`. A
  plugin that imported `untaped_lint.SPEC` or `untaped_lint.build_app` names
  the plugin class `untaped_lint.plugin:LintPlugin`; what other plugins may
  import stays `untaped_lint.api`.
  ([#943](https://github.com/alexisbeaulieu97/untaped/pull/943))
- Replace `--org` with `--organization` and `--inventory-org` with
  `--inventory-organization`, `export --print-paths` with `export --format
  paths`, and `awx schema --format yaml` with `awx schema --yaml`. A script
  that matched a renamed record kind (`awx.job`, `awx.event`, `awx.log`,
  `awx.status`, `awx.test_result`, ...) matches its new name. A script that
  read `jobs events -f json` or `jobs logs -f json` without `--follow` as one
  array reads one object per line. A script that read several columns from
  `-f raw` names them with `--columns`. A script that read a lone document
  from a kind's `export -f json` reads the first item of the list. A script
  that passed `--format` with `export --out -` drops it (stdout is YAML). A
  script that read a `usage` row's `target` as an id reads `target.id`. A rule
  module that imported `untaped_awx.api.AwxResources` imports
  `untaped_awx.api.Resources`. Where a guide was read by name, read the new
  address in the changed entry.
  ([#947](https://github.com/alexisbeaulieu97/untaped/pull/947))
- workspace: read `workspace run --format json` one object per line (`jq -s`
  gathers a list) and sort by `dir` if a script relied on the workspace's order.
  ([#949](https://github.com/alexisbeaulieu97/untaped/pull/949))
- Replace `untaped auth set <plugin>` with `untaped auth login <plugin>` and
  `auth unset` with `auth forget`; instead of `auth migrate`, log in again with
  `auth login`. ([#951](https://github.com/alexisbeaulieu97/untaped/pull/951))
- A plugin calls `connected_client(config, section=..., base_url=...,
  bearer=...)` and declares its token as a `Credential` instead of
  `TokenSources` and a `token` or `token_command` setting.
  ([#951](https://github.com/alexisbeaulieu97/untaped/pull/951))
- Set `awx.server.url` where an 11.x configuration set `awx.base_url`: a 12.0
  home starts from a new `config.yml`, so nothing carries the old key over.
  ([#952](https://github.com/alexisbeaulieu97/untaped/pull/952))
- A plugin writes stderr through `ctx.diag`: `ui.message("info", x)` is
  `ctx.diag.info(x)`, `"warning"` is `ctx.diag.warning(x)`, `"success"` (and
  `ui.success(x)`) is `ctx.diag.success(x)`, and `"error"` is
  `ctx.diag.failure(Failure.plain(x))`. `ui.progress(label)` is
  `ctx.diag.progress(label)`, `report_error(exc)` is `ctx.diag.failure(exc)`,
  and a row's error is `ctx.diag.failure(exc, item=...)`. `echo(x, err=True)`
  is the call for its prefix: `error: ...` is a `Failure.plain`, `warning:`
  and `hint:` are `ctx.diag.warning` and `ctx.diag.hint`, anything else is
  `ctx.diag.info(x, unmuted=True)`. `ui.styled(x, err=True)` is
  `ctx.diag.info(x, unmuted=True)` with any `tail` appended to the `Text`, or
  `ctx.diag.relay(x)` for another tool's output. `project_root(path)` is
  `checkout_root(path) or path.resolve()`, and a test calling
  `check_conventions(name)` calls `untaped_lint.testing.assert_clean(name)`.
  ([#952](https://github.com/alexisbeaulieu97/untaped/pull/952))
- Save a run for `awx test run --compare` with `--format json`, and pipe the
  rows an untaped command printed rather than hand-built ones. A plugin that
  called `read_stdin_input` declares a `Stdin[R]` field and reads
  `self.stdin.values()`, asking `self.stdin.kind(row)` for a row's kind; a
  test that built a `PipeEnvelope` for `convert` passes a row of the
  provider's record to `untaped.sdk.testing.piped_through`.
  ([#953](https://github.com/alexisbeaulieu97/untaped/pull/953))
- A record's `table_columns = ("name", "state")` becomes
  `name: Annotated[str, Column]` and `state: Annotated[str, Column]` on those
  fields. The table shows them in the order json shows the fields. An
  `OutcomeRecord`'s `action` and a `CheckRecord`'s `status` are already marked,
  so they join the record's own marks. To mark another inherited field, such as
  a `TargetRecord`'s `target_path`, redeclare it. A command whose columns its
  record's fields can't give (a subclass that wants none of its base's
  columns, say) names them as the record's fields:
  `Output(columns=(key(Repo).full_name, key(Repo).pushed_at))`.
  ([#955](https://github.com/alexisbeaulieu97/untaped/pull/955))
- `read_identifiers(names, stdin=flag, id_field="id")` becomes a
  `stdin: Stdin[Model]` field whose model marks its identifying field `Key`,
  and `self.stdin.identifiers(names)` in `run`. That call refuses names and
  `--stdin` together, as before. `resolve_text_input(value=..., file=...)`
  becomes a `TextOrStdin` field (`-` reads stdin) or a `PathOrStdin` one for a
  file. ([#957](https://github.com/alexisbeaulieu97/untaped/pull/957))
- `get_config_section("acme")` becomes `self.settings` in a command, or
  `settings_of(AcmeSettings)` elsewhere in the run; a test reads them inside
  `with install(Plugin).running():`. Drop `with report_errors():` around a
  command's body: raise the `UntapedError` and core prints it and exits with
  its code. ([#958](https://github.com/alexisbeaulieu97/untaped/pull/958))
- `Output(columns=("name", "state"))` becomes
  `Output(columns=(key(Repo).name, key(Repo).state))`, and
  `self.output.columns.add("url")` becomes `.add(key(Repo).url)`.
  `self.output.columns[:] = (...)` becomes `.show(...)`,
  `.insert(0, "pack")` becomes `.add(key(Row).pack, first=True)`, and a
  comprehension that removes one becomes `.drop(key(Row).kind)`. A class whose
  own field is named `key` shadows the function inside its body: declare the
  columns as a module constant above it.
  ([#959](https://github.com/alexisbeaulieu97/untaped/pull/959))
- A renderer that called `echo(text)` returns `text`; one that echoed several
  lines returns them as a list; one that called `print_tree(graphs, ui=...)`
  returns the graphs; one that printed `ui.styled(text)` returns the `Text`. A
  renderer that printed exact content (`echo(text, nl=False)`) returns
  `Verbatim(text)`.
  ([#960](https://github.com/alexisbeaulieu97/untaped/pull/960))
- Annotate a terminal parameter `Terminal` and pass `self.ctx.terminal` down to
  code below the command that prompts (awx's suite variables take it this way),
  instead of calling `ui_context()` there. A confirmation is the `Confirm`
  block's `self.confirm(question, preview=...)`. A multi-select is `pick_many`.
  What a command printed with `ui.styled(...)` or `print_tree(...)`, an
  `Output` renderer now returns (below).
  ([#960](https://github.com/alexisbeaulieu97/untaped/pull/960))
- Write `@rule("no-print", reads=Tree, severity="error", summary="...")` as
  `@rule(severity="error")` on `def no_print(tree: Tree)`, with the summary as
  the docstring's first paragraph. A `Linter` takes `findings(self, record)`
  and a `TreeReader` returns the records themselves; tests pass a record to
  `run_rule` and `run` where they wrapped it in `subject(...)`.
  ([#962](https://github.com/alexisbeaulieu97/untaped/pull/962))
- Rename `StaleData` to `Stale` and `PluginCheckRow` to `PluginCheck` where a
  plugin or its tests import them.
  ([#963](https://github.com/alexisbeaulieu97/untaped/pull/963))
- Read `my_rule.id` and `my_rule.severity` where a rule's test read
  `my_rule.rule.id`, and build a hand-written `Linter.rules()` row with
  `untaped_lint.api.rule_row(my_rule)` inside the run. List the records a plugin
  prints that no command names in its plugin class's `records` instead of giving
  them `kind=`. ([#965](https://github.com/alexisbeaulieu97/untaped/pull/965))
- **Plugin authors:** a 12.0 provider requires each plugin whose contract one of
  its `fills` subclasses in its plain `dependencies`, with a range
  (`untaped-shelf>=1,<2`), not under an extra named after it. `untaped lint`
  reports "fills a contract of shelf, which it does not require".
  ([#970](https://github.com/alexisbeaulieu97/untaped/pull/970))
- Move the plugin to a 12.0 plugin class in a leaf `plugin.py` and point its
  entry point at it (`acme = "untaped_acme.plugin:Acme"`), as the quarantine
  line says; a module that no longer imports under 12.0 is quarantined as
  `import-failed` first, naming the import that failed. A test that
  gave `compose_with` a fake through `provides=` composes a plugin class whose
  `fills` holds the fake provider, and one that passed a `PluginSpec` to
  `plugin_candidate` or `assert_surface` passes its plugin class.
  ([#974](https://github.com/alexisbeaulieu97/untaped/pull/974))
- Move each guide file's text into the docstring of the option bundle or enum
  it explains, its first paragraph saying when to read it; it is served at
  `untaped docs PLUGIN guides NAME` with NAME the class name in kebab-case. A
  `.untaped/lint.yml` that ignored `guide-name`, `guide-description` or
  `guide-heading` drops those ids.
  ([#975](https://github.com/alexisbeaulieu97/untaped/pull/975))
- Run `untaped auth login` again for each token you keep, then delete the
  `credentials` section of `~/.untaped/state.yml`: nothing reads it any more.
  The tokens 11.x filed stay in your password store until you delete them there.
  ([#976](https://github.com/alexisbeaulieu97/untaped/pull/976))
- Delete `~/.untaped/state.yml` once you no longer run untaped 11.x on that
  home: 12.0 reads nothing from it, nor from a project's `.untaped/state.yml`.
  A plugin keeps its state as rows its plugin class holds,
  `items = StateRows[Item]()`, keyed by the field marked `Key`; a string map
  becomes rows of a model with a `Key` field and a value field.
  ([#977](https://github.com/alexisbeaulieu97/untaped/pull/977))
- A `.untaped/lint.yml` that ignored one of the removed rule ids drops it.
  ([#978](https://github.com/alexisbeaulieu97/untaped/pull/978))
- Declare a field block instead of each option alias: `output: Output[R]`
  for `--format` and `--columns`, `confirm: Confirm` or `dry_run: DryRun`,
  `stdin: Stdin[R]`, `parallel: Parallel(cap=...)` and `limit: Limit`. `run`
  yields or returns its records instead of calling `emit` or `emit_document`,
  and returns a string instead of calling `echo`; a tree is
  `Output(render={"tree": ...})`. A `KeyValue` or `KeyJson` field replaces
  `parse_kv_pairs` and `parse_json_pairs`, and `ExistingFile` replaces
  `existing_file`.
  ([#979](https://github.com/alexisbeaulieu97/untaped/pull/979))
- Hold each block on your plugin class and leave its plugin and name out:
  `motd = Cache(label=..., max_age=...)`, `token = Credential(key=...)`,
  `repo_store = DoctorCheck(title=..., run=...)`. A store is a numbered place,
  `index = Dir(file="index.sqlite3", layout=2)`, which lives in
  `plugins/<plugin>/index/` (an 11.x store's files are not read), and a cache
  that describes it follows it with `Cache(..., follows=index)`. A
  `Refresh(CACHE)` field replaces `refresh_option`, and `ExecutableCheck` and
  `OnlineCheck` on the plugin class replace `executable_check` and
  `online_check`. Data a setting points at, which `setting_guard` kept from
  cleanup, is a place at that setting: `Dir(at=key(Settings).index_dir,
  layout=2)`. ([#980](https://github.com/alexisbeaulieu97/untaped/pull/980))
- Declare the plugin as a `Plugin` class instead of a `PluginSpec`, its skill as
  `skill = StubSkill("...")` and a deprecated behaviour as `row_kind =
  DeprecatedBehaviour(removed_in=..., replacement=...)` on it. A command reads
  its folder as `self.ctx.plugin.dir` (`Project.plugin_dir(spec)` has no
  successor: build `project.root / ".untaped" / "plugins" / "<plugin>"`), its
  settings as `self.settings` (other code in the run as `settings_of(Model)` for
  `app_context().section(...)`), its stderr and HTTP clients as `self.ctx.diag`
  and `self.ctx.http`, and its files under `.untaped/` as
  `self.ctx.plugin.committed(name, root=...)`. A renamed command keeps its old
  name with `aliases = (Deprecated(replacement="old-name", removed_in=...),)` on
  its class; a renamed flag or group has no declaration yet (file an issue when
  a rename needs one). A deprecated flag carries `deprecated(...)` on its field,
  and mount puts it under Deprecated options. Delete any
  `# untaped: allow foreign-section` marker.
  ([#981](https://github.com/alexisbeaulieu97/untaped/pull/981))
- Drop `kind=` from each record and diagnostic class, and list a model no
  command names in its plugin class's `records` (a diagnostic a command
  emits, a record a provider's bridge reads). Name the class after the kind it
  should keep: `kind="acme.repo_row"` is `class RepoRow(Record)`. A column
  coloured by its name alone (`status`, `action`, `result`, `state`,
  `job_status`) now needs a `Status(...)` on its field.
  ([#982](https://github.com/alexisbeaulieu97/untaped/pull/982))
- A plugin that starts its own `threading.Thread` or `ThreadPoolExecutor`
  starts its workers with `bounded_map` or `Parallel.map`, or runs each target
  in `contextvars.copy_context().run`.
  ([#984](https://github.com/alexisbeaulieu97/untaped/pull/984))
- A script or plugin that matched `owner-not-installed` (or
  `Reason.owner_not_installed`) in `doctor` rows or contract reasons stops
  matching it: no row carries it any more.
  ([#986](https://github.com/alexisbeaulieu97/untaped/pull/986))
- A plugin test that read `current_memory_store()` takes the `credentials`
  fixture instead (`def test_login(credentials: MemoryCredentials)`), and reads
  or files secrets through it (`credentials.entries`, or
  `credentials.set(Hello.token, HelloServer(...), "secret")` after
  `install(Hello)`). A test that installed its own store with `memory_store()`
  loads `untaped.sdk.testing.plugin` instead
  (`pytest_plugins = ["untaped.sdk.testing.plugin"]` in its top-level
  `conftest.py`) and uses the store it installs for every test.
  ([#989](https://github.com/alexisbeaulieu97/untaped/pull/989))
- A test that built `ScriptedPromptBackend(confirms=[True], texts=["Ada"])`
  scripts the `terminal` fixture instead, or builds
  `ScriptedTerminal().answers(True).types("Ada")`: `secrets=` and `texts=` are
  one `types(...)`, in the order the command asks them, `selections=` is
  `selects(...)`, `picks=` and `screens=` are `picks(...)` and `screens(...)`,
  `interrupt=True` is `interrupts()`, and a backend with nothing scripted is
  `present()`. Read `backend.asked` where it read `backend.calls`. A test that
  called `assert_destructive_contract` runs the command twice: once piped
  without `--yes` (it exits 2 and says it `requires --yes`) and once after
  `terminal.answers(False)` (it asks, then changes nothing), or runs a built
  command with `install(Hello).run(command, answer=False)`.
  ([#990](https://github.com/alexisbeaulieu97/untaped/pull/990))
- A command that called `batch_apply(..., destructive=True)` declares
  `confirm: Confirm` and asks `self.confirm(question, preview=...)` before it
  writes; a no prints `cancelled; no changes made` and exits 1. Where it
  called `resolve_each(ids, fn)`, it reports each item's error with
  `self.ctx.diag.failure(exc, item=id)` or yields a row whose `action` failed.
  `finish(any_failed)` needs nothing once each failure is reported that way
  (`raise Failed()` when no row or failure says so), and
  `finish(..., predicate_hit=True)` becomes `raise Matched()`.
  ([#991](https://github.com/alexisbeaulieu97/untaped/pull/991))
- Workspaces are under `plugins/workspace/checkouts/` in the home, not
  `~/.untaped/workspaces`; set `workspace.workspaces_dir` to keep them in
  another folder. A shell prompt segment reads
  `plugins/dotfiles/prompt/attention` (and `status.json`) in the home, or the
  folder `dotfiles.state_dir` names. These folder settings take an absolute
  path (`~` is expanded); a relative one is a config error (exit 4).
  ([#996](https://github.com/alexisbeaulieu97/untaped/pull/996))
- A script reading `github cache delete` or `cache prune` rows reads `action`
  (`deleted`, `released`, `planned` or `failed`) where it read `status`
  (`removed`, `cached`), and finds a failed release as a `failed` row with
  `error`. A script reading `github.corpus_repo` rows finds no `kept` or
  `detail`. ([#997](https://github.com/alexisbeaulieu97/untaped/pull/997))
- **Plugin authors:** where `untaped lint` now reports `layer` on `api.py`,
  `settings.py` or `records.py`, move what that module needs from `commands/`
  into a module of its own that `commands/` imports, or import the command
  inside the function that uses it. Findings that named `cli` imports are gone:
  a plugin's own logic may import its commands.
  ([#1002](https://github.com/alexisbeaulieu97/untaped/pull/1002))
- 12.0 starts from a new home, so nothing 11.x wrote is read: set the 12.0
  settings in the new `config.yml`, and let each plugin fetch or build its
  data again. A plugin that kept a file with `read_data_file` and
  `write_data_file` names it once as `DataFile[Model].at(path)` (from
  `untaped.sdk.storage`) and calls `read()` and `write(value)`: the files it
  reads carry the stamp `write()` puts on them, so `unstamped="accept"` and
  `legacy_stamps=` have no replacement.
  ([#1005](https://github.com/alexisbeaulieu97/untaped/pull/1005))
- A cloned repository's committed `.untaped/config.yml` is read once you run
  `untaped project init` there: read what it sets (its `profile:` and values)
  before you do. A script that reads `untaped profile current -f json` finds
  the project's file under `folder` (or `null`) and the names it skipped
  under `skipped`.
  ([#1004](https://github.com/alexisbeaulieu97/untaped/pull/1004))
- `rejected_token_error("acme", message)` becomes
  `rejected_token_error(Service(AcmeSettings), message)`, called inside the
  run, so the error and its `auth login` hint name the plugin as it runs,
  renamed or not.
  ([#1020](https://github.com/alexisbeaulieu97/untaped/pull/1020))
- A plugin whose docstring or option help names a command in inline code
  (`` `untaped acme sync` ``) keeps that mention true: rename or drop it with
  the command, or `untaped lint` fails on it.
  ([#1025](https://github.com/alexisbeaulieu97/untaped/pull/1025))
- A plugin that derives several commands from one base passes each its own
  `qualname=`, the name its module binds it to (`PATCH = derive(Patch,
  "Patch", qualname="PATCH", ...)`), or composition refuses the plugin with
  `duplicate-target`.
  ([#1026](https://github.com/alexisbeaulieu97/untaped/pull/1026))
- Workspaces made by 12.0 pre-releases before this change aren't read: archive
  or remove them with the pre-release first, or delete their folders. A script
  that read `freed_bytes` from `workspace remove` reads the repo row's
  `detail`; one that matched `skipped` there matches `refused`.
  ([#1012](https://github.com/alexisbeaulieu97/untaped/pull/1012))
- In a plugin's `pyproject.toml`, write each requirement on `untaped` or another
  plugin as a floor: `untaped>=12.0` instead of `untaped>=12,<13`, the owner of
  a contract you fill as `untaped-git>=12.0`. Ignores and allow comments
  naming `lint.provides-requirement` name `lint.requirement-floor`.
  ([#1050](https://github.com/alexisbeaulieu97/untaped/pull/1050))
- Scripts that matched the quarantine reason `owner-out-of-range` read
  `unmet-requirement`; ones that matched `malformed-entry-point` for a plugin
  whose import raised read `import-failed`. Running a disabled plugin's command
  exits 4, not 2.
  ([#1050](https://github.com/alexisbeaulieu97/untaped/pull/1050))
- A provider method whose parameter names differ from its contract method's,
  such as `def lookup(self, _title)` renamed to quiet a linter, one that takes
  a parameter positional-only, or one taking `*args` without `**kwargs`, is
  now quarantined (`uses-missing`): give it the contract's parameter names.
  Declare the contract's full signature, or `**kwargs: Any`, and raise your
  floor on the owner when you read a parameter it added. `assert_surface`
  rewrites a plugin's snapshot in format 3, which untaped 11.x refuses to read
  ("surface format 3 is newer than this generator's"), so a plugin that still
  runs its tests on 11.x skips `assert_surface` there.
  ([#1052](https://github.com/alexisbeaulieu97/untaped/pull/1052))
- A plugin that star-imports untaped or another plugin (`from untaped.sdk
  import *`) fails `untaped lint` (`lint.import-boundary`): import each name,
  so lint can check it against the releases installed.
  ([#1055](https://github.com/alexisbeaulieu97/untaped/pull/1055))
- Replace `version=lambda: code_version("acme.parser")` with
  `version=installed_code("acme.parser")`, imported from `untaped.sdk`. It
  hashes every file of each module or package it names; name a library's
  package (`"yaml"`) where `distributions=` named its distribution. A named
  module that is not installed raises instead of counting as absent, so leave
  an optional library out. `extra=` has no counterpart: combine the strings in
  your own function (`lambda: f"{installed_code('acme.parser')()}:v2"`).
  ([#1065](https://github.com/alexisbeaulieu97/untaped/pull/1065))
- Read a `setup plan` row's `secret` where a script read `by: user`, and stop
  reading `fix_by` from failure records: the hint names the step, and
  `auth login` is the step that asks for a token.
  ([#1070](https://github.com/alexisbeaulieu97/untaped/pull/1070))
- Drop `fix_by=` from the errors a plugin raises; the hint already names the
  command to run.
  ([#1070](https://github.com/alexisbeaulieu97/untaped/pull/1070))
- Move each setting a profile can no longer hold to the base: run
  `untaped --profile NAME config unset KEY`, then
  `untaped config set KEY VALUE`.
  ([#1071](https://github.com/alexisbeaulieu97/untaped/pull/1071))
- A plugin that runs git requires `untaped-git` and imports `run_git`,
  `git_toplevel`, `GitResult` and `GitCommandError` from `untaped_git.api`, and
  its tests import the fixtures from `untaped_git.testing`. `safe_path_segment`
  comes from `untaped.sdk.storage`, where it is experimental.
  ([#1097](https://github.com/alexisbeaulieu97/untaped/pull/1097))
- A configuration that set `jira.api_prefix` or `jira.agile_prefix` for a Jira
  served under a context path puts that path in `jira.server.url` instead
  (`https://example.com/jira`).
  ([#1122](https://github.com/alexisbeaulieu97/untaped/pull/1122))
- Drop `awx.api_prefix` from a profile: untaped detects AWX or AAP on first
  use. Where detection can't reach the server's `ping/`, set
  `awx.server_version`, for example `untaped config set awx.server_version
  "AAP controller 4.6.2"`.
  ([#1123](https://github.com/alexisbeaulieu97/untaped/pull/1123))
- In each recipe's `inputs:`, rename `sensitive: true` to `secret: true`; a
  recipe that still says `sensitive` fails to load, naming the key. Scripts that
  read `recipe get` rows read `secret` instead of `sensitive`. A script that
  checks `recipe apply`'s exit code sees 2 (usage) instead of 1 for an unknown
  `--var` or `--input-from` name, an `--input-from` naming a global input, or an
  `--input-from` expression that doesn't compile.
  ([#1127](https://github.com/alexisbeaulieu97/untaped/pull/1127))
- A plugin that built `GithubClient(settings, http=...)` calls
  `untaped_github.api.github_api(settings)` instead, with the same methods and
  no `with` block: core keeps and closes its connections.
  ([#1120](https://github.com/alexisbeaulieu97/untaped/pull/1120))
- Rank repo providers again with `untaped plugin rank git.repo_source repos
  PLUGIN...` and set a cache age under `git.caches.repo_source_repos`; a script
  that filters piped rows on `workspace.repo` filters on `git.repo`. A plugin
  filling `RepoSource` imports it, `Repo` and `RepoName` from `untaped_git.api`
  and depends on `untaped-git` instead of `untaped-workspace`.
  ([#1132](https://github.com/alexisbeaulieu97/untaped/pull/1132))
- A timer or script that runs `untaped dotfiles apply --yes` treats exit 3 as
  success (`SuccessExitStatus=3` under systemd): it means something waits for
  a person, such as a modified file. To place every turned-on entry whatever
  its policy, as `apply` did before, run `apply --all`. A script that reads
  `status --check` gets 3 only when there is something for `apply` or a
  person to do; a `once` file behind its source no longer counts. To keep a
  `link` item a link, give it the `sync` policy:
  `dotfiles add PKG --policy sync`.
  ([#1137](https://github.com/alexisbeaulieu97/untaped/pull/1137))
- Run `untaped ansible source refresh` once per saved source: the index moves to
  `index.v7.sqlite3` and starts empty. A requirements file that names a role by
  a URL on a host no source's repositories live on (a public `github.com` role,
  with every source on another host) now lists it as unresolved: map it with
  `untaped ansible source-alias set NAME OWNER/REPO` to keep it in the graph.
  ([#1140](https://github.com/alexisbeaulieu97/untaped/pull/1140))
- Drop `--backend` from `untaped ansible` commands. Set `git.probe_parallel`
  instead of `ansible.probe_parallel`, and `github.ref_probe.rate_limit_floor`
  instead of `ansible.source_refresh_rate_limit_floor`; `untaped config migrate`
  deletes the old keys. Live reads (`deps`, `find` and `graph` with no source)
  read 8 repositories at once. There is no way to force `git ls-remote` for a
  host whose plugin probes it: a repository that probe cannot read is read with
  `git ls-remote` on its own.
  ([#1142](https://github.com/alexisbeaulieu97/untaped/pull/1142))
- Save each `untaped ansible` source again from its selector, such as
  `untaped github repos selector --org acme --team acme/platform --format pipe
  | untaped ansible source set platform --stdin`, then refresh it. A script
  that passed `--org`, `--team` or `--repo` to `deps`, `impact`, `find` or
  `graph` saves a source once and passes `--source NAME`. A script reading
  `orgs`, `teams` or `repos` from `source list` reads `selector` instead.
  ([#1151](https://github.com/alexisbeaulieu97/untaped/pull/1151))
- A script passing `--live` to `untaped ansible deps`, `find` or `graph` drops
  it and selects no source: run it where `ansible.default_source` is unset, such
  as in a profile without it. An install of `untaped[ansible]` alone adds a
  plugin that lists repos, such as `untaped[github]`.
  ([#1152](https://github.com/alexisbeaulieu97/untaped/pull/1152))
- Re-export an `awx export --out-dir` backup into an empty folder (or delete its
  old files first): export never removes files, and an old file beside its
  renamed copy is applied too.
  ([#1156](https://github.com/alexisbeaulieu97/untaped/pull/1156))

### Added

- `untaped plugin list` records say what each plugin may import from:
  `requires` (the distributions it requires) and `fills` (the owners whose
  contracts it fills); a quarantined plugin's record names the package its
  entry point gives.
  ([#837](https://github.com/alexisbeaulieu97/untaped/pull/837))
- `untaped.sdk` has the 12.0 plugin interface: `Plugin`, `Command`, `Group`,
  `derive` and `StubSkill`; the fields core fills (`Output`, `Stdin`, `Lines`,
  `Confirm`, `Refresh`, `Parallel`, `Limit`); the value types `File`,
  `ExistingFile`, `TextOrStdin`, `PathOrStdin`, `KeyValue` and `KeyJson`; the
  marks `Positional`, `Short`, `AllowHyphen`, `Column`, `Key`, `Following` and
  `Written`; and `key(Model).field` for a setting key. Core can compose plugin
  classes into one declaration, checking them and naming the class or field a
  problem is about.
  ([#839](https://github.com/alexisbeaulieu97/untaped/pull/839))
- `untaped.sdk` has the run side of the 12.0 plugin interface:
  `settings_of(Model)` and `plugin_of(Model)` name a plugin by its settings
  model (its settings, its folder, its committed files), and
  `setting_name(key(Model).field)` spells a setting's full key; a command reads
  `self.settings` and `self.ctx` (`diag`, `terminal`, `project`, `plugin`,
  `plugins`); and an error's `system` is a `System` (`UNTAPED`, `LOCAL`, `GIT`,
  `HTTP`) or `Service(Model)` for a plugin's own service.
  ([#840](https://github.com/alexisbeaulieu97/untaped/pull/840))
- A 12.0 plugin class names its `Cache`, `Credential`, `DoctorCheck` and
  `DeprecatedBehaviour` by the attribute holding it:
  `motd = Cache(label=..., max_age=...)` is the plugin's cache `motd`, and
  `repo_store = DoctorCheck(title=..., run=...)` is the check
  `<plugin>.repo-store`. `ExecutableCheck("git", purpose=...)` and
  `OnlineCheck(probe=...)` are the program-on-PATH and online checks in that
  form. The plugin's name comes from the run, so these blocks are read inside
  one. ([#843](https://github.com/alexisbeaulieu97/untaped/pull/843))
- An entry point can name a 12.0 plugin class (`hello =
  "untaped_hello.plugin:Hello"`): untaped composes it, builds its command line
  from the declaration (options, arguments, bundles, groups, panels, marks and
  renamed spellings), validates what was typed as the command class (a value
  the model refuses exits 2 naming its flag), and prints what `run` returns.
  In `run`, `self.output` reads the chosen `format` and the table's `columns`
  (`.add(...)` adds more, `.empty(text)` sets what an empty table shows), and
  `self.stdin` reads `identifiers(...)`, `values(...)` or `rows(...)` from the
  arguments or `--stdin`, refusing rows of a kind `Stdin[R]` does not accept
  (`.requested` says `--stdin` was passed, before anything is read).
  A plugin class that composition refuses is quarantined (`bad-plugin-class`)
  with every problem it names; arguments that cannot be filled in field order
  (one after a list argument, a required one after an optional one) are one of
  those problems (`positional-order`), and so is an option spelled as a root
  option: `--profile`, `--verbose`/`-v`, `--quiet`/`-q` or `--deprecated`
  (`root-option`).
  ([#842](https://github.com/alexisbeaulieu97/untaped/pull/842))
- In a 12.0 command's `run`, `self.refresh.directive` says what `--refresh` or
  `--cached` asked, `self.parallel.value` and `self.limit.value` give the
  numbers (`Parallel`'s from its setting when `-j` is not given, clamped to its
  cap with a warning), and `self.confirm(question, preview=..., needed=...)`
  asks once before the command commits: `--yes` goes ahead, `--dry-run` previews
  and returns `False`, and without a terminal it refuses with `NeedsYesError`
  (exit 2) naming the command and its question.
  ([#845](https://github.com/alexisbeaulieu97/untaped/pull/845))
- A 12.0 plugin class's caches, doctor checks, credentials and deprecated
  behaviours reach `untaped config`, `doctor` and `auth` under the names its
  attributes give them (`motd = Cache(...)` is
  `hello.caches.motd.max_age_seconds`), and `untaped plugin schema` prints, and
  suggests, the record kinds its declaration names.
  ([#847](https://github.com/alexisbeaulieu97/untaped/pull/847))
- `tests/repo/surface.txt` lists record kinds and their fields (`record`
  lines): core's stderr kinds now, and a 12.0 plugin class's kinds from its
  declaration. A kind a command reads on `--stdin` or from a file also lists
  its required fields and defaults, so a new required field or a changed
  default fails the surface check.
  ([#848](https://github.com/alexisbeaulieu97/untaped/pull/848))
- A 12.0 plugin class keeps its state rows in its own stamped
  `plugins/<plugin>/state.yml`, written under that file's lock:
  `items = StateRows[Item]()` keys `Item` rows by the one field marked `Key`,
  and a model with no such field makes the rows a list (`all`, `append`,
  `replace_all`). The command that removes a row marks itself
  `removes = ITEMS` and takes the key under the `Key` field's name, as its
  first positional or as an option (`--id KEY`); a damaged row's error offers
  it with the row's key. List rows have one remover too.
  `StateRows[Item].per_project()` keeps rows in a project's
  `.untaped/plugins/<plugin>/state.yml`. Composition refuses rows it cannot
  key, rows named `untaped`, a second command removing the same rows, a
  `removes` naming rows the plugin class doesn't hold and a remover with no
  field named like the rows' key.
  ([#846](https://github.com/alexisbeaulieu97/untaped/pull/846))
- `installed_code(*modules)` names the code a cache's value depends on:
  `Cache(version=installed_code(parsers))` refreshes when a file of that
  module or package changes, and `Cache(version=installed_code())` when
  untaped or any plugin is installed, upgraded, reinstalled or, in an editable
  install, edited. Neither form imports a module to find it.
  ([#849](https://github.com/alexisbeaulieu97/untaped/pull/849))
- `DataFile[Mark].at(path)` reads and writes one stamped file wherever its
  owner places it: `read()` answers found, absent, unreadable or a newer
  untaped's, and `write()` takes the file's lock, keeps the keys the model
  doesn't know and never overwrites a newer untaped's file. A 12.0 plugin
  class declares an operation lock as `sync = Lock(wait=timedelta(minutes=10))`:
  `locked()` waits that long for another process, then fails with a hint to
  retry, `try_locked()` tries once, and taking it again in one thread only
  counts. ([#850](https://github.com/alexisbeaulieu97/untaped/pull/850))
- A 12.0 plugin class declares a folder of its own as `notes = Dir()`
  (`plugins/<plugin>/notes/`), or at a path setting with
  `Dir(at=key(Settings).field)`; `file=` makes it one file and `layout=`
  numbers what it holds inside it, `open()` creates it, and `locked(name)`
  holds one thing in it. Operation locks are taken in one order: a plugin's
  own before that of a plugin it requires. A registered install's reference
  names a numbered place's layout, `prune()` removes the layouts nothing holds
  after the plugin's complete refresh, and doctor's `stores` row lists them.
  `Dir(items=..., release=..., moved=...)` lets holders such as projects hold
  things in an unnumbered place, and `items()` is that item store.
  ([#852](https://github.com/alexisbeaulieu97/untaped/pull/852))
- `Cache(max_age=None, model=..., version=...)` is a version-only cache: a value
  that depends only on the code computing it is filled once per version and
  served from then on, with no max-age setting and nothing on stderr.
  ([#851](https://github.com/alexisbeaulieu97/untaped/pull/851))
- Core keeps the composed declaration of the 12.0 plugin classes under
  `~/.untaped/plugins/untaped/cached/declaration/`, so a start after the first
  reads it instead of importing each plugin class and its commands. Installing,
  upgrading or editing untaped or a plugin composes again on the next start,
  which a terminal shows as `Reading the installed plugins`.
  ([#854](https://github.com/alexisbeaulieu97/untaped/pull/854))
- The example plugin `examples/untaped-hello` is a 12.0 plugin class: `Hello`
  in `untaped_hello.plugin`, its commands as classes, its cache and stub skill
  as attributes; its errors name `System.LOCAL` instead of `hello`. A doctor
  check's `PluginContext` takes `owner=` (core sets it) and has `plugin`, so
  the body reaches its folder as `ctx.plugin.dir`.
  ([#855](https://github.com/alexisbeaulieu97/untaped/pull/855))
- A 12.0 command's outcome row whose `action` is `failed`, `partial` or
  `conflict` sets exit 1 even without an `error`, unless its failure is
  remembered from an earlier run. It raises `Failed()` after its rows when it
  failed in a way the rows do not say (exit 1, nothing more printed) and
  `Matched()` when a check matched (exit 3); a failure the rows counted wins
  over either. Rows already yielded are printed before an error.
  `self.parallel.map(fn, items)` runs `fn` on each item, `-j` at a time, and
  yields `(item, result)` pairs as they finish (`ordered=True` keeps the input
  order), with `on_abort` for the work in flight when it stops early and
  `while_running` for foreground work.
  ([#853](https://github.com/alexisbeaulieu97/untaped/pull/853))
- The SDK's groups list their names: `untaped.sdk.storage`, `.network`,
  `.health`, `.git`, `.projects` and `.prompt` export the blocks and helpers
  each is home to (`from untaped.sdk.storage import Cache`), loading a
  member only when it is first used.
  ([#856](https://github.com/alexisbeaulieu97/untaped/pull/856))
- Cache ages, progress cadence and HTTP back-off all read the run's clock,
  so one test clock moves all three: `untaped.sdk.testing.manual_clock()`
  installs a `ManualClock` that moves on `advance(hours=1)` and on a
  block's sleep, which never waits.
  ([#857](https://github.com/alexisbeaulieu97/untaped/pull/857))
- `untaped.sdk.testing.install` composes plugin classes into the test's home
  with their settings as models: `install(Hello, settings=HelloSettings(...))`
  then `.run(Greet())` runs a built command and `.cli("hello say ada")` a
  command line, both returning one `Ran` (`exit`, `stdout`, `stderr`,
  `rows_as(Model)`). The pytest plugin gains the `install`, `home` and `clock`
  fixtures. ([#858](https://github.com/alexisbeaulieu97/untaped/pull/858))
- The testing kit gains the `terminal` fixture (a `ScriptedTerminal`, absent
  until a test scripts it: `terminal.answers(yes=True)`,
  `terminal.types("Ada")`, `terminal.picks(result)` for a picker,
  `terminal.selects(value)`, `terminal.screens(...)`, `terminal.present()`
  with nothing scripted, `terminal.interrupts()` for Ctrl-C), the
  `credentials` fixture (the `MemoryCredentials` every test runs with:
  `credentials.set(Hello.token, key, "secret")`), `check(install(Hello))`
  (every failing composition row at once) and
  `assert_surface(Hello)` (a plugin class, with the snapshot at
  `snapshots/surface.txt` beside the test unless `path=` says otherwise).
  ([#859](https://github.com/alexisbeaulieu97/untaped/pull/859))
- `Confirm.as_dry_run()` gives the same answers with `--dry-run` on, for a
  command whose `--check` previews.
  ([#861](https://github.com/alexisbeaulieu97/untaped/pull/861))
- `UNTAPED_HOME` moves the home, the one folder untaped keeps everything in
  (`config.yml` and `plugins/<name>/`); it defaults to `~/.untaped`
  and must be an absolute path (`~` is expanded), else exit 4. Plugins read it
  through `untaped.sdk.home()`, and a command reads its own folder as
  `self.ctx.plugin.dir`.
  A plugin may not be named `home`.
  ([#862](https://github.com/alexisbeaulieu97/untaped/pull/862))
- `Output(format="raw")` gives a 12.0 command another default output format
  than `table`, such as `raw` for a one-line answer or `yaml` for a document;
  `--format` still overrides it, and only a `table` default follows
  `UNTAPED_FORMAT` and `untaped.ui.format`. A format the `Output` does not offer
  is refused when the class is defined. `Output(formats=("tree", "json"))`
  offers only the formats it names, each core's or a renderer's, the first the
  default; `--format` refuses the others.
  ([#867](https://github.com/alexisbeaulieu97/untaped/pull/867))
- `Output(render={"raw": …})` replaces a core format with the plugin's own
  renderer, and `Output.requested` tells `run` which `--columns` the run asked
  for (`None` without the flag). Columns a command declares with
  `Output(columns=…)` show in a one-record table even when the record leaves
  them empty. ([#868](https://github.com/alexisbeaulieu97/untaped/pull/868))
- A 12.0 `Group` takes `runs`, the command its own words run, as `Plugin.runs`
  does for a plugin: `untaped doctor` runs the checks and `untaped doctor fix`
  stays its subcommand.
  ([#874](https://github.com/alexisbeaulieu97/untaped/pull/874))
- A 12.0 command that writes without asking previews through `dry_run: DryRun`:
  core adds `--dry-run` alone (no `--yes`, no question, no "(asks first)" tag)
  and the field is true after it. One command takes `Confirm` or `DryRun`, not
  both. core's own `config set|unset|migrate`, `profile create|rename`,
  `plugin rank` and `plugin new` take it.
  ([#875](https://github.com/alexisbeaulieu97/untaped/pull/875))
- `repairs = True` on a 12.0 command class says it repairs untaped's setup: it
  runs under an invalid `UNTAPED_FORMAT` and the per-run skills check skips it.
  Core reads the mark and a command's preview (`--dry-run`, or
  `Confirm.as_dry_run()` during `run`) from the declaration and its blocks,
  never from a command's name or raw tokens. `doctor`, `doctor fix` and every
  `skills` command carry it, so `skills` now also runs under an invalid
  `UNTAPED_FORMAT`.
  ([#875](https://github.com/alexisbeaulieu97/untaped/pull/875))
- An install's reference now lists the plugins it composes (`plugins`, written
  even when empty), by `setup register` and kept current by the install's
  starts: each one compares the file with what it composes and rewrites it when
  they differ, so one that couldn't is retried by the next. A command waits for
  the references' lock first (warning if it stays busy), so a prune elsewhere
  can't miss it; root `--help` and `--version` don't wait. So `setup prune`
  keeps the folder of a plugin another install sharing the home uses, and keeps
  every folder while a live install's reference has no list. `write_reference`
  in `untaped.sdk.testing` takes `plugins=` (left out, the key is too).
  ([#882](https://github.com/alexisbeaulieu97/untaped/pull/882))
- `untaped setup prune` removes what unreachable installs and uninstalled
  plugins left in the home: the references of installs that are not reachable
  (deleted without `setup register --remove`, or on a disk not mounted now),
  and the `plugins/<name>/` folders of plugins not installed here, each whole,
  with its data, caches and state. It lists both, then asks once (`--dry-run`
  previews, `--yes` skips the question, and off a terminal without `--yes` it
  exits 2). A folder stays, as a `kept` row, while a live reference names its
  plugin (another install sharing the home composes it, or a project holds its
  items), while any reference cannot be read, while a live install's reference
  doesn't list its plugins, or when it is a symlink. Each item is checked again
  under the references' lock before it goes; one that can't be removed fails
  the run while the others still go, and a busy lock fails them all (exit 5,
  retry later). It writes `untaped.prune_outcome` rows.
  ([#882](https://github.com/alexisbeaulieu97/untaped/pull/882))
- In `untaped.sdk.testing`, `with hello.running() as run:` runs a block as a
  command of that install runs, and `run.diagnostics` are the typed stderr
  lines the block itself shows, as `Ran.diagnostics` are a command's.
  ([#891](https://github.com/alexisbeaulieu97/untaped/pull/891))
- `untaped.sdk.testing.install` takes `providers={"fake": (FakeBooks,)}`, which
  composes the provider classes a test defines as a plugin named `fake` (its
  settings are the providers' `Provider[S]`, so `settings=[S(...)]` reaches
  them), and composes the owners an installed provider requires, so
  `install(Library)` brings `shelf`, whose section `settings=` can
  write too. ([#907](https://github.com/alexisbeaulieu97/untaped/pull/907))
- Commands and providers build their HTTP clients with the experimental
  `self.ctx.http.client(self.settings, base_url=..., bearer=...)`, which
  also takes `verify=` and `headers=` (`untaped.sdk.network.HttpFactory`):
  core adds the proxy, trust, timeout and retries, binds the bearer to its
  origin and attributes failures to the plugin whose settings it is given.
  A client class takes the factory (`AcmeClient(settings, http=self.ctx.http)`);
  its tests take the testing kit's (`with install(Acme).running() as run:`,
  `run.http`). ([#911](https://github.com/alexisbeaulieu97/untaped/pull/911))
- A record field marked `Following` (a list of models) prints in table format
  as a table of its own after the first, its columns read from the items'
  model and headed by the field's name in words (`pull_requests` as
  `pull requests`); among several rows, each row's table follows and its
  heading names the row's `Key` value (`comments of ABC-1`). json, yaml and
  pipe keep the field inside the record. Composition refuses `Following` on a
  field that is not a list of models.
  ([#911](https://github.com/alexisbeaulieu97/untaped/pull/911))
- The testing kit's `plugin_candidate` and `compose_with` take a plugin class.
  ([#922](https://github.com/alexisbeaulieu97/untaped/pull/922))
- `Parallel(default=key(S).parallel, unset=8, cap=...)` reads an optional
  setting: unset, it takes `unset`, lowered to the cap without a warning.
  ([#927](https://github.com/alexisbeaulieu97/untaped/pull/927))
- `untaped.sdk.now()` is the run's wall time in UTC, the one clock a plugin
  reads: the testing kit's `clock.advance(hours=3)` moves it with core's own
  stamps, cache ages and back-offs. Outside a run it raises.
  ([#928](https://github.com/alexisbeaulieu97/untaped/pull/928))
- The experimental `self.ctx.http.proxy_for(url)` and `self.ctx.http.trust`
  give a plugin untaped's proxy and CA bundle for a tool core doesn't build
  the client of (`git`), refusing a folder's proxy or trust setting as
  `ctx.http.client` refuses it.
  ([#930](https://github.com/alexisbeaulieu97/untaped/pull/930))
- `Limit(default=30, cap=1000)` caps `--limit`: a larger value is clamped to
  the cap with a warning, as `Parallel`'s cap is, and the option's help says
  "up to 1000". ([#930](https://github.com/alexisbeaulieu97/untaped/pull/930))
- A record field can say how a table shows it, where json, yaml, raw and pipe
  keep the value: `Annotated[int, Bytes]` reads as a size (`1.5 KiB`),
  `Annotated[datetime, Age]` as how long ago it was (`3 h ago`, by the run's
  clock), and `Annotated[dict[str, int], Spread]` as one column per key, which
  `--columns KEY` picks. A command names its `Spread` keys once it is built (a
  model validator calling `self.output.columns.add(...)`, or `.offer(...)` for
  a key hidden by default), so `--columns` is checked before `run`, and a named
  key no row has is an empty column; `--columns` picks only the keys it named.
  `untaped.sdk.ago_text(at, now)` gives the same words for a time elsewhere.
  ([#930](https://github.com/alexisbeaulieu97/untaped/pull/930))
- `Cache(follows=index)` ties a plugin class's cache to a numbered `Dir` of the
  same class: its files live in `cached/<name>.v<layout>/`, move with the
  place's layouts, and a complete fill prunes the place. A `Dir(at=)` whose
  optional path setting is left unset is the default place,
  `plugins/<plugin>/<attribute>/`.
  ([#932](https://github.com/alexisbeaulieu97/untaped/pull/932))
- `self.stdin.kind(row)` names the kind a piped row was read as, so a command
  taking `Stdin[Record]` can report where each row came from.
  ([#932](https://github.com/alexisbeaulieu97/untaped/pull/932))
- Plugin authors can mark a field `Duration` (seconds, shown as `1m42s`) or
  `ObjectName` (a full 40-hex git object name, shown as its first 10
  characters) for tables, as they mark `Bytes` and `Age`; json, yaml, raw and
  pipe keep the value.
  ([#934](https://github.com/alexisbeaulieu97/untaped/pull/934))
- `untaped docs` prints the documentation of the untaped you run, generated from
  what core and each plugin declare: `untaped docs` explains the addresses and
  lists every plugin, `untaped docs PLUGIN` what to read first and its sections,
  and `untaped docs PLUGIN commands|inputs|outputs|settings|extensions|guides
  [NAME]` one page each. `--search TEXT` prints every matching line with its
  page's address, or says on stderr that none matched (a blank one exits 2); an
  unknown address exits 2 naming the page that lists what exists. Pages render
  at a terminal and are raw Markdown when piped.
  ([#936](https://github.com/alexisbeaulieu97/untaped/pull/936))
- Every `--help` screen ends with the address of its page: "Full
  documentation: untaped docs ...".
  ([#936](https://github.com/alexisbeaulieu97/untaped/pull/936))
- Plugin authors can say how a table colours a field's words with `Status`:
  `Annotated[RepoAction, Status(success=("cloned",))]` names the theme role
  (`success`, `warning` or `error`) each word is drawn in, so a user theme
  recolours them. A field adds to the roles of the field it redeclares, so
  `OutcomeRecord.action` keeps core's outcome words and `CheckRecord.status`
  its check words.
  ([#937](https://github.com/alexisbeaulieu97/untaped/pull/937))
- `Confirm.ensure_answerable(question)` refuses (exit 2) before a command does
  the slow or side-effecting work its question follows, when nothing could
  answer it: no terminal, no `--yes` and no `--dry-run`.
  ([#939](https://github.com/alexisbeaulieu97/untaped/pull/939))
- Plugin authors can mark an option that names a document the plugin finds
  and reads itself (a library name or a path) with `Document(M)`:
  `Annotated[Positional[str], Document(RecipeFile)]`. Core reads nothing and
  checks nothing, but lists `M` among the command's inputs, as it does for a
  `File[M]` option.
  ([#939](https://github.com/alexisbeaulieu97/untaped/pull/939))
- `DryRun.go_ahead(preview)` writes on stderr what a `--dry-run` would do, as
  `Confirm` previews, and says whether to write.
  ([#941](https://github.com/alexisbeaulieu97/untaped/pull/941))
- A hint names a command by its class: `run_hint(Set.spelled(key(...), "URL"))`,
  `hint(...)`, `command_line(...)` (now exported) and an error's
  `hint=Login.spelled("github")` spell it as the run mounts it, so a renamed
  command or key follows its class. `untaped.core.api` exports the core
  commands a plugin names (`Set`, `Login`, `Rank`), and lint's
  `import-boundary` rule admits it. `http_of(Settings)` is a plugin's
  `self.ctx.http` for its own code another plugin's command calls, as
  `settings_of` is for its settings.
  ([#941](https://github.com/alexisbeaulieu97/untaped/pull/941))
- A nested model that defines `table_text()` (the `TableText` protocol) shows
  that text as its table cell, as a file location reads `src/a.py:3`; json,
  yaml and pipe keep the nested record.
  ([#943](https://github.com/alexisbeaulieu97/untaped/pull/943))
- `Stdin.text(what=)`, experimental, reads stdin's whole text for an argument
  whose `-` the command tells from a value itself (`workspace run NAME -`);
  with `--stdin`, or from a terminal, it is a usage error (exit 2).
  ([#945](https://github.com/alexisbeaulieu97/untaped/pull/945))
- A command page whose `--stdin` reads providers' own records through a
  contract's bridge says so, naming the extension point's page.
  ([#945](https://github.com/alexisbeaulieu97/untaped/pull/945))
- `untaped.sdk.Context` and `untaped.sdk.Diag` name the types of a command's
  `self.ctx` and `self.ctx.diag`, for a helper that takes them.
  ([#947](https://github.com/alexisbeaulieu97/untaped/pull/947))
- A `bool | None` option (unset unless given) takes `--no-NAME` as well as
  `--NAME`, so a command can tell false from unset.
  ([#947](https://github.com/alexisbeaulieu97/untaped/pull/947))
- `Output(stream=True)` prints each row as `run` yields it, for a command that
  follows a log: `json` one object per line, `yaml` one document per row, a
  table its header once, and a plugin renderer once per row.
  ([#947](https://github.com/alexisbeaulieu97/untaped/pull/947))
- `untaped.sdk.read_stdin_text()` reads the text piped to a command, for a
  `PathOrStdin` given `-`; `PathOrStdin` also marks list items
  (`Positional[list[PathOrStdin]]`). A `-` is refused (exit 2) from a
  terminal, and beside `--stdin`, before the command runs; a `TextOrStdin`
  given `-` is the piped text, and an empty pipe is refused (exit 2, naming the
  option). One `-` per command line reads stdin.
  ([#947](https://github.com/alexisbeaulieu97/untaped/pull/947))
- `Output(columns=...)` takes field references, `key(Repo).full_name`, so a
  renamed or missing field fails where the command is declared. A nested field
  is its dotted column, `key(CaseResult).failure.message` for
  `failure.message`. A reference to a field of a model other than the command's
  record (or a base it inherits) is refused.
  ([#955](https://github.com/alexisbeaulieu97/untaped/pull/955))
- `ctx.diag.info(rows)` prints records on stderr as one table, laid out like a
  stdout table: their `Column` fields, in the run's theme, with empty columns
  left out and the list view where the theme asks for it. A `Confirm` or
  `DryRun` preview can return rows the same way (`untaped.sdk.PreviewItem`).
  Under `-q` the table is muted unless `unmuted=True`; JSON diagnostics carry
  it as plain `key: value` text.
  ([#961](https://github.com/alexisbeaulieu97/untaped/pull/961))
- A plugin class lists the record models it prints that no command names in
  `records`, now a reserved name; each takes the kind
  `<plugin>.<snake_case of the class>` of the plugin whose package defines it.
  In a run, `kind_of` and `record_model` read the kinds composition gave, and
  `record_model` returns any model; before or without a run, the diagnostics
  in `untaped.sdk.diagnostic.DIAGNOSTICS` keep theirs.
  ([#964](https://github.com/alexisbeaulieu97/untaped/pull/964))
- A validator names a command by its class with
  ``InvalidValue("use `{}`", Login.spelled())``: pydantic reports it as any
  `ValueError`, and its text spells the command as the run that reports it
  mounts it. ([#994](https://github.com/alexisbeaulieu97/untaped/pull/994))
- `Credential.login` is the `untaped auth login` command that files a
  credential (`auth login awx`, `auth login git hosts`), and
  `Credential.login_args` what `auth login` and `auth forget` take to name it,
  so a plugin's hint spells the command the way core does.
  ([#998](https://github.com/alexisbeaulieu97/untaped/pull/998))
- `untaped.sdk.parse_utc` reads an ISO 8601 time from a payload as an aware
  UTC `datetime`, or `None` for anything else, the way untaped reads its own
  stamps. ([#999](https://github.com/alexisbeaulieu97/untaped/pull/999))
- `DoctorResult.fix` takes a command named by its class (`Refresh.spelled()`),
  so a doctor check's fix follows the command when it is renamed or mounted
  elsewhere. Core's own hints, fixes and help lines now name its commands
  this way too, and the setup screen's doctor hint leaves out
  `--profile default` when no profile was picked, as doctor's own lines do.
  ([#1000](https://github.com/alexisbeaulieu97/untaped/pull/1000))
- A plugin marks a setting that locates data every project shares
  `Annotated[Path | None, HomeOnly()]` (`untaped.sdk`, experimental), so a
  project's `.untaped/config.yml` can't set it; `git.store_dir`,
  `workspace.workspaces_dir`, `ansible.index_dir`, `recipe.library_dir` and
  dotfiles' folders carry it.
  ([#1004](https://github.com/alexisbeaulieu97/untaped/pull/1004))
- A project's `.untaped/config.yml` (experimental) is a settings layer: a run
  inside the project reads its `profile:` (a name or a list) and its values,
  above the base and beneath `UNTAPED_PROFILE`, `--profile` and `UNTAPED_*`
  variables, so a command's pick stacks on the folder's. The file counts only
  in a folder this home recorded as a project (a copied one warns until
  `project init` adopts it), and only when it and its folder are yours and
  nobody else can write them; a secret, a
  command or a setting only the home holds there exits 4, and an unknown
  profile name is skipped with a warning. `config set|unset --project` write it,
  `config get|list` name it as `project:<name>`, `profile rename` and `delete`
  rewrite the projects that pick the profile, `project init` records the run's
  `--profile` or `UNTAPED_PROFILE` pick as the new project's `profile:`, and a
  missing credential's hint names the run's pick (`untaped --profile <x> auth
  login <plugin>`). `doctor` checks the file first, and it and the docs pages
  warn when the project's pyproject pin admits only an untaped without
  projects, which ignores the file.
  ([#1004](https://github.com/alexisbeaulieu97/untaped/pull/1004))
- `untaped.sdk.projects.init_project(path)` (experimental) makes a folder a
  project as `untaped project init` does, recording the run's explicit profile
  pick, and answers `Initialized` with the names it `picked`;
  `remove_project(project)` removes one as `untaped project remove` does, and
  answers `ProjectRemoval`: how many items it still `held` (then nothing was
  removed), else what it `kept` in the folder. A refused row from
  `Project.release` carries the owner's hint alone: the plugin's command says
  how its user forces it. `Project.release(leaving=...)` previews a project's
  turn in a batch the same command confirmed: the projects let go of before it
  count as no other holder, and `untaped project prune` uses it, so a repo two
  pruned projects share is released by the second.
  ([#1012](https://github.com/alexisbeaulieu97/untaped/pull/1012))
- A field of a pydantic model in a plugin's Python API can carry
  `deprecated(removed_in=...)` in its annotation: the surface file dates it. A
  model that keeps unknown fields (`extra="allow"`) may drop the field when
  due; one that refuses them (`extra="forbid"`) keeps it until the model goes,
  and one that ignores them (`extra="ignore"`) refuses the mark, since a
  caller's value would vanish silently once the field goes.
  ([#1053](https://github.com/alexisbeaulieu97/untaped/pull/1053))
- A renamed name of `untaped.sdk` or one of its groups keeps its old spelling
  until the major its deprecation names: the old name is the new object, warns
  on use, and type checkers accept it. A renamed contract method keeps working
  for providers that still fill it by its old name.
  ([#1054](https://github.com/alexisbeaulieu97/untaped/pull/1054))
- A deprecated name a plugin's code looks up or calls while a command runs
  warns once per run, naming the plugin in the sentence and in the
  `untaped.deprecation` line's new `plugin` field; the SDK's modules warn at
  the lookup itself, an import included. `untaped doctor` lists each as a
  `plugin-uses` row, as it does a deprecated contract method a provider
  fills. `untaped lint` reports both, and each deprecated name a plugin
  imports, as `uses-deprecated` warnings, and a star import of untaped or
  another plugin as an `import-boundary` error. A plugin that fails to load
  because untaped or another plugin lacks a name it uses is disabled alone
  (`uses-missing`), naming the file and line, the owner's installed version,
  what the plugin requires of it, and both fixes.
  ([#1055](https://github.com/alexisbeaulieu97/untaped/pull/1055))
- `untaped doctor` lists a deprecated name a plugin's code was seen using
  while a command ran, such as a method on an object it got back, which no
  import shows: its `plugin-uses` row names the command and the release that
  removes the name. untaped records each one in its own state, once per plugin
  version, name and command, without ever failing or slowing the command.
  ([#1062](https://github.com/alexisbeaulieu97/untaped/pull/1062))
- A plugin command that reaches a name untaped or another plugin no longer has
  (a method on an object it got back, a module looked up while it runs) stops
  with an error naming the plugin, the name, the owner's installed version,
  what the plugin requires of it and both fixes, and exits 4, instead of a
  traceback; `--verbose` shows the traceback. A contract provider that reaches
  one answers with the same error.
  ([#1066](https://github.com/alexisbeaulieu97/untaped/pull/1066))
- `untaped.sdk.process` runs a child process in its own process group, to the
  end with `run_process` or long-lived with `start_process`, and stops the whole
  group on a timeout, an interrupt or a request to stop: one soft signal (the
  interrupt's own, or SIGTERM), then SIGKILL two seconds later.
  ([#1074](https://github.com/alexisbeaulieu97/untaped/pull/1074))
- Plugins that call an upstream themselves can honour the upstream budget:
  `capped()` and `succeeded()` in `untaped.sdk.network` (experimental) give the
  timeout cap `doctor --online` and a cache's automatic refresh set, and lift
  it after a success.
  ([#1097](https://github.com/alexisbeaulieu97/untaped/pull/1097))
- `config get`, `config set` and `config unset` reach one entry of a map
  setting and the settings inside it (`config set untaped.ui.symbols.success
  Y`), and a plugin names one with `key(Settings).servers["main"]`.
  ([#1095](https://github.com/alexisbeaulieu97/untaped/pull/1095))
- A plugin opens a repo store repository with `plain_clone=True`
  (`RepoStore.for_url`, `RepoStore.owned_by`) to give its worktrees the layout
  of a clone (`origin/*`, the remote's tags, `origin/HEAD`), which only
  workspace had; one plugin per repository holds it.
  ([#1099](https://github.com/alexisbeaulieu97/untaped/pull/1099))
- A plugin writes its own settings through
  `self.ctx.plugin.plan_settings({key(Settings).name: value})` (experimental):
  the changes are checked together before anything is written, `preview()`
  feeds `Confirm(preview=)`, and `write()` writes where `config set` writes, a
  `HomeOnly` setting to the base.
  ([#1101](https://github.com/alexisbeaulieu97/untaped/pull/1101))
- `untaped_git.api.clone(url, dest, branch=)` (experimental) clones a working
  copy the user owns afterwards, with the credential of whichever plugin fills
  `GitHost` for the host and untaped's proxy; the clone's config keeps neither
  them nor a credential helper.
  ([#1108](https://github.com/alexisbeaulieu97/untaped/pull/1108))
- Plugins can keep their commands independent of the server API version with
  the experimental `ServerApi` block (`untaped.sdk.network`): commands call one
  port, core detects the server's version, caches it per server for a day,
  picks the adapter serving it, refuses a version none serves with exit 4, and
  after a mismatch switches adapter and reruns the call when nothing took
  effect. Each block adds a `<plugin>.server-api` doctor row (`--online`
  detects again), a "Servers" section on the plugin's `untaped docs` page and
  a `<plugin>.caches.server_api.max_age_seconds` setting.
  ([#1111](https://github.com/alexisbeaulieu97/untaped/pull/1111))
- A plugin's `DoctorCheck(network=...)` says when its check runs: `"offline"`
  (the default), `"online"` (only where untaped may contact services:
  `untaped doctor --online`, `untaped setup plan --online` and setup's check of
  new values) or `"both"`, reading `PluginContext.online` to tell which.
  ([#1116](https://github.com/alexisbeaulieu97/untaped/pull/1116))
- `untaped jira` asks its server which Jira it runs on first use and again after
  a day (`jira.caches.server_api.max_age_seconds`), refuses Jira Cloud and
  releases below 8 with exit 4, and shows what it found in a `jira.server-api`
  doctor row; `jira.server_version` says the version instead of detecting it.
  ([#1122](https://github.com/alexisbeaulieu97/untaped/pull/1122))
- The `values` plugin (`untaped[values]`, brought by `recipe` and `awx`) holds
  the inputs language plugins share: `--var`, `--vars-file`,
  `--non-interactive`, prompts and secret inputs. `untaped docs values guides
  vars` is its guide.
  ([#1127](https://github.com/alexisbeaulieu97/untaped/pull/1127))
- github detects whether its server is github.com or GitHub Enterprise Server,
  and which release, and refuses a GitHub Enterprise Server older than 3.9 with
  exit 4; `github.server_version` (`github.com` or `GitHub Enterprise Server
  3.14.2`) sets it instead of detecting, and `untaped doctor` shows it in a
  `github.server-api` row.
  ([#1120](https://github.com/alexisbeaulieu97/untaped/pull/1120))
- A contract can declare a record bridge, a method taking a `ProviderRecord`,
  so a provider issues a second record of its own (a selector, say) whatever
  its `T`; `own(item, cls)` reads it back typed, and `gather(plugins=)` answers
  `not-filled` for a named plugin that doesn't fill an optional method.
  ([#1134](https://github.com/alexisbeaulieu97/untaped/pull/1134))
- git's `RepoSource` gains `to_selector`, which turns a provider's own selector
  record into a `git.repo_selector`.
  ([#1134](https://github.com/alexisbeaulieu97/untaped/pull/1134))
- `untaped dotfiles` merges TOML files: a `merge` file whose target ends in
  `.toml`, or that says `format: toml`, writes the keys its source names into
  the target and keeps the target's other keys, comments and order. A new table
  is written inline where a `[header]` would capture the keys after it, and a
  merge that can't be written without changing other keys writes nothing.
  ([#1136](https://github.com/alexisbeaulieu97/untaped/pull/1136))
- `untaped ansible impact checkouts ROLE` lists what checks out each dependent
  of a role, from every plugin that fills ansible's new `CheckoutSource`
  contract: awx fills it with its job templates, so installing awx now installs
  ansible, too. ([#1139](https://github.com/alexisbeaulieu97/untaped/pull/1139))
- `untaped dotfiles apply` runs a package's steps: a check (a command and
  what it prints, a path, or a program on PATH), then the step's program or
  source script when the check fails, then the check again; a step with only
  a `fix` is reported. Steps show in the one preview, and nothing runs
  before the confirmation. They follow only a policy the machine chose, else
  `manual`, and `status` lists them (`kind: step` rows) as `pending`, `new`,
  `applied`, `drifted`, `behind`, `unmet` or `orphan`, running only checks
  that were approved; `status.json` counts `drifted` and `unmet`. A step's
  own inputs are asked each run and never kept, a secret reaches the program
  only on stdin, and what a run prints is shown with secrets masked.
  `remove`, `unsubscribe` and turning an item on again forget its steps
  without undoing them.
  ([#1141](https://github.com/alexisbeaulieu97/untaped/pull/1141))
- `untaped dotfiles apply` clones an item's `repos:` entries: a working copy
  whose target is absent is cloned once through the git plugin, with its
  credentials and proxy and nothing left in the copy's config, and then left
  to the person. A checkout of the entry's URL already there is `applied`, and
  anything else there is `foreign` and never touched. `status` lists them as
  `kind: repo` rows, and `remove` and `unsubscribe` forget them and leave the
  copies in place.
  ([#1145](https://github.com/alexisbeaulieu97/untaped/pull/1145))
- `untaped dotfiles show` with no selector at a terminal opens a screen to
  browse every package: `space` turns a package or item on or off, `p`
  cycles its policy, `e` answers a plain input, and `ctrl-s` saves every
  change to `config.yml` at once, said line by line after it closes. esc
  closes it saving nothing. A named format (`--format json`, or
  `UNTAPED_FORMAT` or `untaped.ui.format` set), `--columns`, or a stdout or
  stdin that isn't a terminal (`show | grep starship`) prints the rows as
  before. ([#1146](https://github.com/alexisbeaulieu97/untaped/pull/1146))
- `Output` tells a command two facts about its run (experimental):
  `format_chosen`, whether the run named its format (`--format`,
  `UNTAPED_FORMAT` or `untaped.ui.format`), and `to_terminal`, whether stdout
  is a terminal. A command that opens a screen instead of rows checks both, as
  a pager does, so a pipe or a named format still gets rows. Tests fake a
  terminal on stdout with `invoke_cli(..., stdout_terminal=True)` or
  `Install.cli(..., stdout_terminal=True)`.
  ([#1146](https://github.com/alexisbeaulieu97/untaped/pull/1146))
- `untaped_git.api.probe_refs(urls, kinds=, default_branch_only=)`
  (experimental) reads many repositories' branches and tags. It asks the plugin
  that serves each host once for all of that host's URLs, through the optional
  `GitHost.probe_refs` (experimental), and reads the rest with `git ls-remote`,
  `git.probe_parallel` at once: URLs on a host with no probe, a URL that probe
  could not read for any reason but "not found", and every URL of a host whose
  probe was unavailable. It reports each host's budget. github fills the probe
  with its batched GraphQL reads and says the budget is spent below
  `github.ref_probe.rate_limit_floor`.
  ([#1142](https://github.com/alexisbeaulieu97/untaped/pull/1142))
- `untaped github repos selector --org/--team/--repo` prints a
  `github.repo_selector` without listing anything or needing a token. Piped
  where a `git.repo_selector` is read, it is kept as github's own record, and
  `untaped_git.api.select_repos(plugin, selector)` (experimental) asks that
  plugin for its repos through the optional `RepoSource.select` (experimental;
  answers cached by `git.caches.repo_source_select`). A selector with only
  `match` patterns, or one whose plugin doesn't fill `select`, takes the
  plugin's `repos()` instead, and `match` narrows either answer.
  `NotReady(methods=("name", ...))` lets a provider name the contract methods
  it can't answer yet; `gather`, `Stdin(via=)` and `untaped plugin check` ask
  it for the others, and doctor names the waiting methods. A name the
  contract doesn't have makes the provider not ready for any method.
  ([#1148](https://github.com/alexisbeaulieu97/untaped/pull/1148))
- `untaped.sdk.contracts.providers(Contract)` (experimental) lists every offer
  to fill a contract, each provider class with its own record and that record's
  kind or why it was refused, so an owner can ask the one provider a file names
  with `gather(..., plugins={offer.provider})`.
  ([#1147](https://github.com/alexisbeaulieu97/untaped/pull/1147))
- A dotfiles step can use a kind another plugin provides (`kind:
  macos.defaults`, its fields under `with:`), which that plugin adds by filling
  the experimental `StepKind` contract from `untaped_dotfiles.api` with a record
  of its own and which dotfiles asks only for a step this machine approved.
  ([#1147](https://github.com/alexisbeaulieu97/untaped/pull/1147))
- An extension point's docs page (`untaped docs PLUGIN extensions NAME`) lists
  the records its providers issue, with their fields.
  ([#1147](https://github.com/alexisbeaulieu97/untaped/pull/1147))
- `untaped plugin list` gives a quarantined plugin's `reason` and `detail`.
  ([#1147](https://github.com/alexisbeaulieu97/untaped/pull/1147))
- `RepoStore.locked()` (experimental) holds a repo store repo's lock across
  several calls, such as a read, a fetch and a write, so they act as one step.
  ([#1156](https://github.com/alexisbeaulieu97/untaped/pull/1156))

### Changed

- **Breaking (plugins):** `untaped.contracts` is now `untaped.sdk.contracts`
  and `untaped.testing` is now `untaped.sdk.testing`; core's other modules
  moved under `untaped.sdk`, `untaped.core` and `untaped.runtime`. Each SDK
  name has one spelling: a group's names (caches, state rows, directories,
  data files and the file helpers in `storage`; the HTTP client and
  credentials in `network`; doctor checks and setup marks in `health`; the
  editor, prompts and picker in `prompt`; `git`, `projects`, `contracts`) are
  imported from the group only, and `untaped.sdk` keeps what a plugin's own
  files need (the plugin, its commands and fields, records and words,
  errors). The screen names leave the SDK for `untaped.screen`, and names only
  core used (`read_stdin`, `table_row`, `HttpSettings`, `connected_client`,
  `Written`, `HolderState`, the holds' bookkeeping) are no longer exported;
  every name left has a first-party user or says who its first will be.
  ([#836](https://github.com/alexisbeaulieu97/untaped/pull/836))
- **Breaking (users):** the setting `http.ca_bundle` is now
  `untaped.http.ca_bundle_path` (`UNTAPED_UNTAPED__HTTP__CA_BUNDLE_PATH`), as
  every path setting ends in `_dir` or `_path`.
  ([#837](https://github.com/alexisbeaulieu97/untaped/pull/837))
- **Breaking (plugins):** the `untaped plugin list` record class is now
  `untaped.sdk.InstalledPlugin` (its kind `untaped.installed_plugin`), since
  `untaped.sdk.Plugin` is the plugin base class. `untaped.sdk.Key` is now the
  record mark; the screens' key press message is `untaped.screen.core.KeyPress`.
  ([#839](https://github.com/alexisbeaulieu97/untaped/pull/839))
- **Breaking (plugins):** `DoctorCheck` and `DeprecatedBehaviour` are no
  longer dataclasses: their arguments (`title`, `run`, `online`) are
  keyword-only, and two of them are equal only when they are the same
  object. ([#843](https://github.com/alexisbeaulieu97/untaped/pull/843))
- HTTP back-off waits on the run's clock alone: a test's `clock` fixture makes a
  retry instant, so patching `untaped.sdk.network.http._sleep` is no longer
  needed (the function is gone). A cache refreshed at the same instant a later
  run starts is refreshed again under `--refresh`, so a test's stopped clock
  never serves a stale copy. `cache_clock` moves its wall clock on a block's
  wait instead of waiting.
  ([#859](https://github.com/alexisbeaulieu97/untaped/pull/859))
- **Breaking (users):** `untaped plugin list --contracts` is now
  `untaped plugin contracts`, and the records `plugin list` and `plugin
  contracts` print are `untaped.installed_plugin` and `untaped.contract_method`
  (were `untaped.plugin` and `untaped.contract`): a record's kind is its class
  name. Core composes its own commands first, as a plugin of itself, so a
  plugin named `untaped`, after a root command or after a config key is
  quarantined as `duplicate-name` (was `reserved-name`); the names nothing
  holds any more (`core`, `sdk`, `contracts`, `plugins`, `default`, `state`)
  are free. ([#860](https://github.com/alexisbeaulieu97/untaped/pull/860))
- `untaped project` (experimental) is now built on core's own plugin class.
  `project list` prints `untaped.recorded_project` records (was
  `untaped.project`); `move`, `remove` and `prune` ask through `Confirm`, so
  without a terminal and `--yes` they exit 2 naming the question they ask.
  Agent pages mark a command built on a plugin class `(asks first)` when it
  takes `Confirm`, and give it neither `(changes things)` nor `(destructive)`:
  `project move`, `remove` and `prune` say `(asks first)`, `project init` and
  `plugin rank` and `new` carry no tag.
  ([#861](https://github.com/alexisbeaulieu97/untaped/pull/861))
- **Breaking (plugins):** in `untaped.sdk.testing`, `home.path` is now the
  untaped home (it was `HOME`), and the hermetic environment no longer sets
  `UNTAPED_CONFIG`.
  ([#862](https://github.com/alexisbeaulieu97/untaped/pull/862))
- **Breaking (users):** untaped's own settings moved into its `untaped`
  section, as any plugin's sit in its own: `http.*`, `ui.*` and `skills.*` are
  now `untaped.http.*`, `untaped.ui.*` and `untaped.skills.*` in a profile, and
  their variables `UNTAPED_UNTAPED__HTTP__*`, `UNTAPED_UNTAPED__UI__*` and
  `UNTAPED_UNTAPED__SKILLS__*`. The `shell` section is gone, `setup` shows one
  `untaped` section, and `http`, `ui`, `skills` and `shell` are free as plugin
  names. ([#863](https://github.com/alexisbeaulieu97/untaped/pull/863))
- **Breaking (users):** `config.yml` is format 2: its top level is the base
  (`default`), one section per plugin, and named profiles sit under `profiles:`.
  Every write starts the file with `format_version: 2`. A non-empty
  `config.yml` without the stamp, or stamped `format_version: 1`, was written by
  11.x and is refused (exit 4) with one line naming the file: 12.0 has no
  upgrade path, so delete it and run `untaped setup`, or keep untaped 11.1 for
  that home. `profiles.default` and `active:` in a format 2 file are refused
  too. `default` names the base and cannot be created, renamed or deleted.
  ([#865](https://github.com/alexisbeaulieu97/untaped/pull/865))
- **Breaking (users):** a run stacks profiles: `UNTAPED_PROFILE` and
  `--profile` take comma lists (and `--profile` repeats), layered over the base
  in order, the variable's names first and the later name winning.
  `--profile default` reads the base alone and sets the variable aside;
  `default` inside a longer list, a malformed name or an unknown name exits 2
  (unknown names list the known ones in `details.known`). Writes land in the one
  profile `--profile` names, else the base, never in what `UNTAPED_PROFILE`
  picks; `--profile a,b` on a write exits 2 with `details.profiles`. `profile
  current` prints the pick comma-joined (or `default`) and its `--format json`
  is `{name, profiles: [{name, source}], folder, skipped}`; `profile list`
  and `profile show` say `selected` instead of `active`; provenance labels the
  base `base`.
  Doctor's fix lines name the last picked profile, so a fix lands where the run
  reads it. Profile names are letters, digits, `_`, `.` and `-`, not starting
  with `.` or `-`; `profile create` and `rename` refuse others (exit 2). No
  write leaves the run's pick naming an undefined profile: `profile delete` and
  `rename` refuse a picked profile (exit 2), and with an undefined pick only
  `profile create` of the missing name writes.
  ([#866](https://github.com/alexisbeaulieu97/untaped/pull/866))
- **Breaking (users):** `untaped profile` is now built on core's own plugin
  class. `profile show` prints an `untaped.profile_view` record (`name`,
  `selected`, `raw`, and the settings under `data`) in every output format,
  YAML by default, instead of the bare settings and a `# profile:` line on
  stderr. `profile list` keeps `untaped.profile`; `profile current` prints
  `raw` by default as before. `profile delete` asks through `Confirm`, so
  without a terminal and `--yes` it exits 2 naming the question it asks, and
  agent pages mark it `(asks first)`; `profile create` and `rename` carry no
  tag. ([#867](https://github.com/alexisbeaulieu97/untaped/pull/867))
- `untaped config` is now built on core's own plugin class. Its commands,
  output and exit codes are unchanged; `config get` gains `--columns`, and agent
  pages no longer tag `config set`, `unset` and `migrate` `(changes things)`.
  ([#868](https://github.com/alexisbeaulieu97/untaped/pull/868))
- **Breaking (users):** `untaped auth` is now built on core's own plugin class.
  `auth login` and `auth forget` take a key field the plugin's settings don't
  hold as `--key FIELD=VALUE` (repeated for several fields, each once) instead
  of `--FIELD VALUE`. `auth forget` asks through `Confirm`, so off a terminal
  without `--yes` they exit 2 naming the question. Agent pages tag `auth forget`
  `(asks first)` instead of `(destructive)`, and no longer tag `auth login`
  `(changes things)`. A broken `untaped.ui` setting no longer fails `auth list`
  or `auth status`. Commands, output and exit codes are otherwise unchanged.
  ([#869](https://github.com/alexisbeaulieu97/untaped/pull/869))
- `untaped skills` is now built on core's own plugin class, and core's `untaped`
  skill is declared by that class like any plugin's. Rows, kinds and exit codes
  are unchanged. `skills remove` asks like core's other groups: the prompt reads
  `Remove N skills?` after the paths, `--dry-run` lists the paths on stderr, a
  removal that fails prints a `failed` row with its `error:` line, and agent
  pages tag it `(asks first)`. A `--project-dir` that is not a directory says
  `--project-dir: not a directory: PATH` (still exit 2).
  ([#871](https://github.com/alexisbeaulieu97/untaped/pull/871))
- The `untaped-hello` example is in the 12.0 layout: one module per command
  under `commands/`, tests that mirror the package under `tests/untaped_hello/`,
  and no directory migration (12.0 starts from a clean home).
  ([#873](https://github.com/alexisbeaulieu97/untaped/pull/873))
- `untaped doctor` and `doctor fix` are now built on core's own plugin class.
  Rows, kinds, exit codes and the checklist are unchanged. `doctor fix` asks
  like core's other groups: the prompt reads `Run N automatic fixes?` after the
  plan, off a terminal without `--yes` it exits 2 naming that question, and
  agent pages tag it `(asks first)`. Under `--format json`, `yaml`, `pipe` or
  `raw`, and with `--columns ?`, the summary and hint lines on stderr now come
  before the rows on stdout; a fix that fails or is partial still ends with
  doctor's hint when a check fails afterwards.
  ([#874](https://github.com/alexisbeaulieu97/untaped/pull/874))
- **Breaking (users):** core's own doctor checks are `DoctorCheck` attributes
  on its plugin class, so their rows are named like any plugin's:
  `untaped.config-permissions` (was `config`), `untaped.unknown-keys` (was
  `unknown-keys`) and `untaped.token-store` (was `config`), which now shows a
  passing row when no stored credential uses `pass`. They run with the other
  plugins' checks. The `caches` row also reads core's own cache of the
  composed plugins.
  ([#876](https://github.com/alexisbeaulieu97/untaped/pull/876))
- Every command runs under its invocation's one run, so a block such as `Dir`
  works under it. With an `UNTAPED_HOME` that doesn't resolve, a command that
  reads the home (`plugin list`, `doctor`) exits 4 naming it; one that never
  does (`untaped --help`, `--version`, `config --help`) still runs.
  ([#877](https://github.com/alexisbeaulieu97/untaped/pull/877))
- **Breaking (users):** the install and project references live in
  `~/.untaped/plugins/untaped/refs/`, core's `refs` folder, instead of
  `~/.untaped/refs/`. Each file is a data file stamped like every other file
  untaped keeps (`"untaped": {"file": 1, "by": "<version>"}`), readable by its
  owner only, and a writer waits on the folder's own lock: a busy one says
  `untaped.refs is busy (another untaped process)`.
  ([#878](https://github.com/alexisbeaulieu97/untaped/pull/878))
- In `untaped.sdk.testing`, `compose_with(...)` runs its block under a run of
  that composition, as a command of it runs, and `make_project` and
  `write_reference` open a run over the hermetic home when none is active.
  ([#878](https://github.com/alexisbeaulieu97/untaped/pull/878))
- A command's stderr keeps one format from its first line to its last: a warning
  untaped prints before the command parses (a quarantined plugin, a deprecated
  command's notice, a busy references lock) follows the command's format as its
  own lines do, so under JSON output it is a JSON line, not a text line among
  them, and under text output it uses the `untaped.ui.theme`. `UNTAPED_FORMAT`
  and `untaped.ui.format` set that format only for a command whose `--format`
  defaults to `table`; one with another default (`profile show`, YAML) or no
  `--format` keeps text on stderr unless the line gives `--format`, so
  `UNTAPED_FORMAT=json untaped profile show --bogus` now reports in text where
  11.1 printed a JSON line. A line naming no command (root `--help`,
  `--version`, an unknown command) follows `--format`, then `UNTAPED_FORMAT`,
  then `untaped.ui.format`. An error in a root option (a malformed `--profile`,
  `-v -q`) follows only `--format` and `UNTAPED_FORMAT`. An invalid
  `UNTAPED_FORMAT` under a command that reads it exits 2 before anything else
  runs, quarantine warnings included.
  ([#882](https://github.com/alexisbeaulieu97/untaped/pull/882))
- `untaped setup` is now built on core's own plugin class. Its commands,
  options, records and exit codes are unchanged, and every `setup` command,
  `setup prune` included, is marked `repairs`: it still runs under an invalid
  `UNTAPED_FORMAT`, and the per-run skills check now skips it too. Agent pages
  no longer tag `setup register` `(changes things)`.
  ([#882](https://github.com/alexisbeaulieu97/untaped/pull/882))
- Under `--format json`, `yaml`, `pipe` or `raw`, `untaped setup`'s summary and
  closing lines on stderr now come before the rows on stdout, and a setup
  screen that saved nothing prints `[]` rather than nothing.
  ([#882](https://github.com/alexisbeaulieu97/untaped/pull/882))
- `untaped doctor`'s `references` row now warns about the reference of an
  install that is not reachable, with `setup prune` as its fix. A new
  `plugin-folders` row warns about the `plugins/<name>/` folder of a plugin not
  installed here that no live reference names, with the same fix, and lists
  the folders that stay with why. The `caches` row no longer notes such a
  folder's `cached/`.
  ([#882](https://github.com/alexisbeaulieu97/untaped/pull/882))
- `untaped doctor --help` says it checks the health of untaped, not "the
  shell", and of every composed plugin.
  ([#898](https://github.com/alexisbeaulieu97/untaped/pull/898))
- **Breaking (users):** `untaped plugin list` (and `installed_plugins()` and a
  command's `ctx.plugins`) lists untaped itself as the plugin `untaped`, with
  untaped's version, marked by the new `root` field of
  `untaped.installed_plugin` records: `true` for it, `false` for every plugin
  installed into untaped.
  `untaped plugin list -f pipe | untaped lint --stdin` skips that row.
  ([#899](https://github.com/alexisbeaulieu97/untaped/pull/899))
- **Breaking (plugins):** a provider subclasses `Provider[S]` (was
  `Configured[S]`) beside the contract it fills, or plain `Provider` when its
  plugin reads no settings, and reaches `self.ctx` (`diag`, `project`,
  `plugin`, `plugins`, no terminal). A plugin class lists the contracts it owns
  in `offers` and its providers in `fills`; core reads each provider's owner
  from the contract and builds it at the first ask.
  ([#907](https://github.com/alexisbeaulieu97/untaped/pull/907))
- The example contract owner and provider (`examples/untaped-shelf`,
  `examples/untaped-library`) are 12.0 plugin classes, `Shelf` offering
  `BookSource` and `Library` filling it, with tests that mirror their packages.
  ([#907](https://github.com/alexisbeaulieu97/untaped/pull/907))
- **Breaking (plugins):** a contract method's types are its declaration.
  `@bridge` and `@listing` are gone: a method returning a `list` is a listing,
  whose invalid rows are dropped one by one, and the method taking `T` and
  returning the owner's model is the bridge (a contract has at most one).
  `@cached(max_age=..., label=...)` makes the answer cache, named
  `<contract>_<method>` and owned by the plugin offering the contract (one
  plugin offers a contract); its setting stays
  `<owner>.caches.<contract>_<method>.max_age_seconds`. `untaped lint` reports
  a malformed rule as its own failed row and keeps the provider's other rules.
  ([#908](https://github.com/alexisbeaulieu97/untaped/pull/908))
- **Breaking (users):** `untaped jira` is now built on a 12.0 plugin class.
  `jira issues comment` reads its body only from `--body` or `--body-file`;
  `--body -` reads stdin, and a piped body without it exits 2. `jira issues get`
  prints an issue's links, when it has some, and with `--comments` its
  comments, as tables of their own after the issues', each headed by its
  issue's key when there are several. In `jira issues transition` a key that
  fails is a row with `action: failed` and its `error`, so a pipe into
  `--stdin` carries it too. The guide to jira's writes is in `jira issues
  --help`. Some usage messages are worded anew.
  ([#914](https://github.com/alexisbeaulieu97/untaped/pull/914))
- **Breaking (plugins):** `gather(method, plugins=, refresh=, progress=)`
  loses `needs=` and `deadline=`, and `plugins=` takes provider classes as well
  as plugin names. A provider's failed answer is `Errored` (was `Failed`), and
  `Answers` has the views `.ok`, `.errored` and `.skipped`. A skip's or
  error's `reason` is a `Reason` (`Reason.not_configured`), typed by its use;
  the words on the wire are unchanged. `NotReady(reason, key(S).field)` takes
  a typed settings key, which core spells in full as `NotReady.spelled`.
  `Contract.http` is gone: a provider builds clients with
  `self.ctx.http.client(...)`. A contract method may be named `http`,
  `gather`, `convert`, `on` or `providers`.
  ([#915](https://github.com/alexisbeaulieu97/untaped/pull/915))
- **Breaking (plugins):** a contract's records take their kind from their
  class name at composition, under the plugin that lists them (the owner's
  `offers`, the provider's `fills`): the owner's model and a provider's own
  record declare no `kind=`, and the `foreign-kind` reason is gone from
  contracts. `Stdin(via=Contract.bridge)` reads the rows of any
  provider's own record through that provider's bridge, building the provider
  only when one of its rows arrives; an unready provider whose row arrived
  exits 4. `convert`, `PipeEnvelope`, `is_envelope_line` and
  `parse_envelope_line` are gone: `Stdin` reads every pipe line, so no plugin
  reads one itself.
  ([#917](https://github.com/alexisbeaulieu97/untaped/pull/917))
- **Breaking (plugins):** `untaped.sdk.contracts.providing({name: classes})`
  takes provider classes (each a `Provider` subclass of the contract it fills)
  and no owner string: it composes a plugin per key whose `fills` are those
  classes, each filling the contract it subclasses, as an installed plugin's
  `fills` do. A class that is no provider raises `TypeError` naming it. lint's
  `Rules` is a `Provider`, and `Rules.from_module(...)` returns the host class
  rather than an instance.
  ([#918](https://github.com/alexisbeaulieu97/untaped/pull/918))
- **Breaking (users):** `untaped dotfiles` is now built on a 12.0 plugin class.
  Its state lives in `plugins/dotfiles/state.yml` and its lock in
  `plugins/dotfiles/changing.lock`, beside the rest of its files, so
  `dotfiles.state_dir` holds only `status.json` and `attention`. Under
  `--dry-run`, `apply` and `remove` show their plan on stderr as they do before
  asking (JSON info lines under `--format json`, where 11.x printed text);
  `apply --dry-run` warns of a failed fetch, and exits 1 on a planned
  `conflict` or `failed` row as a run does. The package and policy guides are
  the docstrings of `PackageFile` and `Policy`, linked from the dotfiles
  README. ([#920](https://github.com/alexisbeaulieu97/untaped/pull/920))
- lint's `protocol-location` rule takes a `Protocol` in any module named
  `ports`, so a plugin laid out flat keeps its ports in `ports.py`;
  `application/ports.py` still passes.
  ([#920](https://github.com/alexisbeaulieu97/untaped/pull/920))
- **Breaking (users):** `untaped git` is now built on a 12.0 plugin class.
  `git store`'s record kind takes its class name, `git.store_report` (was
  `git.store`); `git hosts` still writes `git.host`. `git credential` answers
  nothing, with a warning, for a helper line without `--nonce`: the helper
  lines 11.0 wrote are gone with the 11.x home, and the
  `git.nonceless-credential-helper` behaviour is removed.
  ([#922](https://github.com/alexisbeaulieu97/untaped/pull/922))
- **Breaking (plugins):** a doctor check's body leaves `DoctorResult(id=...)`
  out: core names the row after the `DoctorCheck` it belongs to, and `id`
  is keyword-only (`ok` is the first positional field). A body that passes an
  id still gets a failed row when it is not its check's. `ok` must be a bool:
  a row whose body returns another value fails.
  ([#925](https://github.com/alexisbeaulieu97/untaped/pull/925))
- **Breaking (plugins):** `RepoStore.write_worktree_config` in `untaped_git.api`
  takes no `profile` keyword: the credential helper line it writes names no
  profile; the project the worktree is in picks it through its `profile:`, and
  `UNTAPED_PROFILE` in the shell git runs from.
  ([#928](https://github.com/alexisbeaulieu97/untaped/pull/928))
- **Breaking (users):** `untaped workspace` is now built on a 12.0 plugin class,
  `WorkspacePlugin`. `workspace status` writes `workspace.repo_status` (was
  `workspace.status`); the other kinds keep their names. The workspace
  lifecycle guide is on `untaped workspace --help` and `untaped docs
  workspace`. `workspace run --stdin` reads whole records only, so a piped row
  missing a field is refused before anything runs. Retired settings
  `workspace.cache_dir` and `workspace.protocol` are no longer recognised. The
  credential helper line a worktree's git config gets names no profile: the
  folder's project picks it (its `profile:`), and `UNTAPED_PROFILE`.
  `workspace create` and `workspace add` print their rows only: the
  workspace's path is no longer the last stdout line, so `cd "$(untaped -q
  workspace create ...)"` no longer works.
  ([#928](https://github.com/alexisbeaulieu97/untaped/pull/928))
- **Breaking (users):** `untaped github` is now built on a 12.0 plugin class;
  its record kinds keep their names. `github sweep --show files` and `--show
  matches` are the commands `github sweep files` and `github sweep matches`,
  with the same options. `repos list --fork` and `search repos --fork` take
  `include` (the default), `exclude` or `only`: `--no-fork` is `--fork
  exclude`, and a bare `--fork` is `--fork only`. The scopes guide is the
  docstring of `Scope` now, served at `untaped docs github guides scope`.
  `fetched_at` on `github.corpus_repo` and `github.sweep_repo` rows is a
  timestamp, shown in a table as an age; `github.corpus_repo` gains `detail`
  (what a delete freed, or what kept a repo) and its `disk_bytes` is the
  stored copy's size. The tables show sizes, ages and per-predicate counts,
  and `cache status` says "5 min ago" where it said "5 minutes ago".
  `sweep -c` with an unknown column is refused (exit 2) before anything is
  fetched, as other tables' unknown columns are; it only warned before. A
  `search --limit` above GitHub's 1000 results is clamped to 1000 with a
  warning. `github.token`, `github.token_command`, `github.base_url` and
  `github.sweep.max_age_seconds` are retired: they are no longer read, and
  `untaped doctor` names them for `untaped config migrate` to delete.
  For plugin authors: `GithubSettings` has no `base_url`, `token`,
  `token_command` or `token_sources`.
  ([#930](https://github.com/alexisbeaulieu97/untaped/pull/930))
- **Breaking (users):** `untaped ansible` is now built on a 12.0 plugin class;
  its record kinds keep their names. `ansible impact ROLE --uses NAME` is the
  command `ansible impact uses ROLE NAME...`, with the same options. The index
  lives in a folder: `ansible.index_dir` (unset: `plugins/ansible/index/` under
  the home) holds `index.v<layout>.sqlite3`, and its cache is
  `ansible.caches.source_data` (it was `ansible.caches.index`), so a stale line
  names `ansible.source_data`. Sources and aliases are rows of
  `plugins/ansible/state.yml`, an alias as `{alias, repo}`. A damaged row
  doesn't stop `source remove` or `source-alias remove`, which delete it (a
  damaged alias's outcome has no `repo`). `source patch`'s flags list under
  their own help panel. A row piped into `find` must be of a kind an installed
  plugin declares. `-c` with an unknown column is refused (exit 2) before
  anything runs, as other tables' are; on `source-alias set` it only warned
  after the change before. `ansible.index_path` and
  `ansible.stale_after_seconds` are retired: they are no longer read, and
  `untaped doctor` names them for `untaped config migrate` to delete. The
  resolution and source-data guides are the docstrings of the plugin class and
  of `SourceData`, on `untaped docs ansible` and at `untaped docs ansible guides
  source-data`. ([#932](https://github.com/alexisbeaulieu97/untaped/pull/932))
- A bundle's flags list under its class name as words: a `SourceData` bundle's
  help panel is "Source data", not "SourceData".
  ([#932](https://github.com/alexisbeaulieu97/untaped/pull/932))
- **Breaking (plugin authors):** a table no longer formats a cell by its
  column's name: a field named `*_s` or `elapsed` is a run time only when marked
  `Duration`, and one named `sha`, `commit`, `revision` or `scm_revision` is
  shortened only when marked `ObjectName`. untaped's own records carry the
  marks, so their tables look as before. awx's job record now declares `elapsed`
  and `scm_revision`, and its project and unified template records
  `scm_revision`, so those keys come before AWX's other fields in json, yaml and
  pipe. ([#934](https://github.com/alexisbeaulieu97/untaped/pull/934))
- **Breaking (plugin authors):** a table no longer colours a cell because its
  column is named `status`, `action`, `result`, `state` or `job_status`: it
  colours the words its field's `Status` names, and core's outcome and check
  records name theirs. Free-text fields such as a jira issue's `status` stay
  plain. ([#937](https://github.com/alexisbeaulieu97/untaped/pull/937))
- **Breaking (users):** `untaped recipe` is now built on a 12.0 plugin class;
  its record kinds keep their names. The library is `plugins/recipe/library/`
  under the untaped home unless `recipe.library_dir` is set, so it follows
  `UNTAPED_HOME`; a relative `recipe.library_dir` is refused (exit 4) instead of
  read against the current folder, and every command creates the library folder.
  `apply --stdin` reads a row of any kind an installed plugin declares, or of a
  `null` kind: its target is its absolute `target_path`, else its `path`, and a
  `.summary` row is no longer skipped. A row of a kind no installed plugin
  declares is refused, as is a row its kind's model does not validate (exit 1),
  and a row that names no target is reported as `line N:`. A declined `apply`
  prints no `cancelled` rows. `hooks run` prints a transform's content (or
  `--diff`) and a validate's `STATUS: MESSAGE` in its default `text` format;
  other formats print the `recipe.hook_run` row, which carries an `error` on a
  `fail` verdict; `--columns` needs one of those formats (exit 2 under `text`).
  `hooks run --content -` with an empty pipe runs the transform on empty content
  instead of exiting 1. `get` shows a recipe's inputs and steps, and `backups
  get` a bundle's files, as tables under the record; list cells such as an
  input's values read `a, b`. `backups restore` prints its outcome row above the
  success line. The empty-library hint is a diagnostic in every format, and
  `test -f json` with no cases prints `[]`. A missing `--vars-file` or
  `--args-file`, and a negative `--hook-timeout`, are usage errors (exit 2). The
  guides are at `untaped docs recipe guides hooks`, `guides inputs` and `guides
  hook-contract`, and the recipe file reference at `untaped docs recipe inputs
  recipe_file`. ([#939](https://github.com/alexisbeaulieu97/untaped/pull/939))
- **Breaking (plugins):** `GitHost` keeps `home` and `credential`; its `proxy`
  method and `HostAuth.proxy` are gone. The repo store asks core for
  `untaped.http.proxy` itself, so Git fetches over HTTPS from every host go
  through it, not only those from the GitHub host.
  ([#941](https://github.com/alexisbeaulieu97/untaped/pull/941))
- Hints that name a command quote it in backticks, and the `plugin rank` hint
  for a credential tie starts with "run" like every other.
  ([#941](https://github.com/alexisbeaulieu97/untaped/pull/941))
- **Breaking (users):** `untaped lint` is now built on a 12.0 plugin class; its
  flags, record kinds and exit codes keep their meaning. A `lint.rule` row's
  `id` pattern takes the public `<key>.<id>` that `lint rules` prints, so `lint
  rules -f json` reads back as rules. A table of findings shows each finding's
  target on every row, and its location as `src/a.py:3`. A rule a repository's
  lint file loads with a dotted id is dropped as a malformed rule (exit 1),
  since the run prefixes the key. A hint reads run `untaped lint rules`,
  spelled from the installed commands. The guides are at `untaped docs lint
  guides findings` and `guides rule-module`, and the lint file reference at
  `untaped docs lint inputs lint_file`.
  ([#943](https://github.com/alexisbeaulieu97/untaped/pull/943))
- A `-` that reads text from stdin, given with `--stdin` on the same command,
  is a usage error (exit 2): one stdin holds the text or the rows.
  ([#945](https://github.com/alexisbeaulieu97/untaped/pull/945))
- `--help`'s usage line names each argument, an optional one in brackets
  (`untaped workspace run NAME_OR_CMD [CMD] [OPTIONS]` instead of `[OPTIONS]
  NAME_OR_CMD [ARGS]`), as the command's page does; a group whose own words run
  a command shows both lines.
  ([#945](https://github.com/alexisbeaulieu97/untaped/pull/945))
- **Breaking (users):** `untaped awx` is now built on a 12.0 plugin class. Its
  guides are the docstrings of its option bundles and records: `selecting` is
  `untaped docs awx guides selection`, `launching` is `guides waiting` (and
  `awx job-templates launch --help`), `documents` and `workflow-nodes` are
  `untaped docs awx inputs document`, and `agent-profile` is `untaped docs awx
  commands test`. Record kinds follow their classes: `awx.job` is
  `awx.job_record`, `awx.event` `awx.job_event_row`, `awx.log`
  `awx.job_log_line`, `awx.status` `awx.ping_status`, `awx.apply_outcome`
  `awx.apply_outcome_row`, `awx.cancel_outcome` `awx.job_cancel_outcome`,
  `awx.relaunch_outcome` `awx.job_relaunch_outcome`, `awx.test_result`
  `awx.case_result`, `awx.test_case` `awx.suite_case` and `awx.template_usage`
  `awx.workflow_usage`; `awx.resources` keeps its name. `--org` and
  `--inventory-org` are gone (`--organization`, `--inventory-organization`).
  An option a kind can't use is unknown there (exit 2), such as `--with-scm`
  on `projects list` or `--verbosity` on `workflow-templates launch`.
  `export --print-paths` is `export --format paths`, and `schema --format
  yaml` is `schema --yaml`. `jobs events` and `jobs logs` stream: `json` is
  one object per line and `yaml` one document per row, followed or not, and a
  log row is its `line`, then its `job`, and an event row starts with its
  `counter` and `event` (`-f raw` prints the counter; `-c
  counter,event,host_name,task` gives 11.x's raw line). `jobs events` draws
  AWX-style event lines in a table even without `--follow`; `--columns` gives
  the column table. `test run --source-ref` prints its results after the
  copies are torn down. Under `--yes` a write no longer
  prints its preview (`--dry-run` shows it). `raw` prints each row's first
  column unless `--columns` names more. A kind's `export --format json` prints
  a list, even of one document, and `export --out -` refuses a `--format`
  other than `yaml` (exit 2) instead of ignoring it. `test init` prints the
  suite's path as its result. A `usage` row's `target` is the queried template
  (`id`, `name`, `type`) instead of its id, and its `top_level` says no
  workflow runs it; a target in no workflow is said on stderr, in every format,
  instead of drawn alone by `--format tree`.
  ([#947](https://github.com/alexisbeaulieu97/untaped/pull/947))
- **Breaking (plugin authors):** `untaped_lint.api` offers the `Findings`
  bundle (`--select`, `--ignore`, `--strict`) a lint command takes,
  `lint_report`, which runs the engine, and its `Report`; `lint` and the
  `IgnoreOption`, `SelectOption` and `StrictOption` spellings are gone.
  ([#947](https://github.com/alexisbeaulieu97/untaped/pull/947))
- **Breaking (users):** `workspace run` prints each repo's row as it finishes,
  so rows come in finishing order, with repos `--fail-fast` never started last.
  `--format json` prints one object per line and `yaml` one document per row,
  instead of one list at the end, and an empty selection prints nothing. The
  per-repo blocks are unchanged; `--columns` streams core's table in finishing
  order. ([#949](https://github.com/alexisbeaulieu97/untaped/pull/949))
- **Breaking (users):** a piped row is read whole, as its kind's record, before
  a command acts on it: one that is incomplete, has a value of the wrong type or
  is of a kind no installed plugin declares exits 1 naming its line.
  `untaped lint --stdin` stops on such a row instead of checking the others.
  `awx test run --compare FILE` reads the saved `--format json` output of a
  run, no longer `--format pipe`. Empty stdin says `nothing received on stdin`.
  ([#953](https://github.com/alexisbeaulieu97/untaped/pull/953))
- Default table columns follow the order json shows a record's fields, so some
  tables list them in another order. An outcome's `action` follows the field
  naming the row (`project` outcomes, `project remove`'s preview), or comes
  first where that field is not shown (`project move` and `release`). A check's
  `status` comes after its own columns. Among them: `auth status`, `doctor`,
  `setup plan`, `setup register`, `ansible source status`, `ansible deps` and
  `impact`, `awx jobs events`, `awx` `launch` and `sync`, `awx apply`, `github
  repos list` and `search code`, and `lint` and `awx lint`, where each finding's
  `location` now comes last. `plugin new` and `lint new` show every field of
  their rows. The json, yaml, pipe and raw output is unchanged.
  ([#955](https://github.com/alexisbeaulieu97/untaped/pull/955))
- `skills install`, `status`, `update` and `remove --stdin` also read the rows
  `skills list` and `skills status` print with `--format pipe`, beside bare
  skill names: `untaped skills status -f pipe | untaped skills update --stdin`.
  ([#957](https://github.com/alexisbeaulieu97/untaped/pull/957))
- **Breaking (plugins):** a table column is a field reference. A string column
  in `Output(columns=...)` or `self.output.columns` that names one of the
  record's fields is refused, with the `key(...)` to write instead. So is a
  string naming no field on a record that has no `Spread` field and doesn't
  keep undeclared keys. A declared column is checked when the plugin loads (a
  `field-type` composition problem), and a column `run` adds is checked when it
  is added. A string still names a key no field declares: a `Spread` key, or a
  key an `extra="allow"` record carries (an AWX row's `launch_type`).
  `self.output.columns` is no longer a list: it doesn't index or compare equal
  to one (`list(...)` gives the names), and `run` changes it with `.add(...)`
  (`first=True` to put them first), `.drop(...)` and `.show(...)`.
  ([#959](https://github.com/alexisbeaulieu97/untaped/pull/959))
- **Breaking (plugins):** an `Output(render={...})` renderer returns what to
  print, and core prints it in the run's theme. A string prints as it is, then a
  newline, and an `untaped.sdk.Verbatim` string prints byte for byte with
  nothing added; a Rich `Text` prints in colour where stdout supports it; a
  `TreeGraph` prints as a themed tree, a line too wide for the terminal ending
  in an ellipsis, and trees next to each other print as one block. A list
  prints each in order, and `None` prints nothing. A streamed output calls the
  renderer per row and prints each return.
  ([#960](https://github.com/alexisbeaulieu97/untaped/pull/960))
- `recipe apply`'s preview prints its tables like every other table, in the
  `untaped.ui` theme (borders, the list view, a cell too wide for the terminal
  cut with an ellipsis), and a target that failed planning shows why under
  `detail`. ([#961](https://github.com/alexisbeaulieu97/untaped/pull/961))
- **Breaking (plugin authors):** a lint rule is declared with
  `@rule(severity=...)` alone: its id is the function's name in kebab case
  (`no_print` is `no-print`), its summary the docstring's first paragraph, and
  what it reads the record model its first parameter is annotated with.
  `Linter.findings` takes that record and `TreeReader.read` returns records;
  `Rules` gives each provider its own copy. Lint's layering check flags
  `settings_of` outside `cli/` where it named `get_config_section`, which
  plugins no longer import.
  ([#962](https://github.com/alexisbeaulieu97/untaped/pull/962))
- **Breaking (plugin authors):** `untaped.sdk.StaleData` is `Stale` and
  `untaped.sdk.testing.PluginCheckRow` is `PluginCheck`, so each class is
  named as its kind (`untaped.stale`, `untaped.plugin_check`); the kinds are
  unchanged. ([#963](https://github.com/alexisbeaulieu97/untaped/pull/963))
- **Breaking (plugin authors):** a function `@rule` declares carries its `id`,
  `severity`, `summary` and `model`; its `Rule` row is built when lint loads its
  provider, inside the run, so the kind it reads comes from composition. awx,
  lint and the hello example name no `kind=` on their records; the kinds on the
  wire are unchanged.
  ([#965](https://github.com/alexisbeaulieu97/untaped/pull/965))
- `untaped-github`'s `git` and `workspace` extras, and `untaped-awx`'s `lint`
  extra, are gone: github requires git and no longer uses workspace, and awx
  requires lint. ([#970](https://github.com/alexisbeaulieu97/untaped/pull/970))
- `untaped plugin new NAME --fills OWNER.CONTRACT` writes a 12.0 plugin: a
  plugin class in `plugin.py` (the entry point names it) filling the contract,
  and tests mirroring the package (`tests/untaped_<name>/test_plugin.py` and
  `tests/untaped_<name>/providers/test_<owner>.py`). Its `pyproject.toml` sets
  mypy to report deprecated calls, and its README gives the two test runs a
  plugin keeps: on the lowest versions it declares, and on the newest.
  ([#973](https://github.com/alexisbeaulieu97/untaped/pull/973))
- **Breaking (users):** the index of `auth login`s lives in core's own
  `~/.untaped/plugins/untaped/state.yml`, as its `credentials` rows, instead of
  under `credentials.index` in `~/.untaped/state.yml`. Logins filed by 11.x are
  no longer read: run `untaped auth login` again for each token you keep. A
  damaged row offers `untaped auth forget --id ID`, which forgets the row even
  when it can't be read.
  ([#976](https://github.com/alexisbeaulieu97/untaped/pull/976))
- **Breaking (users):** each plugin keeps its state in its own
  `~/.untaped/plugins/<plugin>/state.yml`; a `state.yml` at the top of the home
  was written by 11.x and is refused (exit 4). A project's `.untaped/state.yml`
  is no longer core's: `project init` no longer ignores it in git and
  `project remove` leaves it.
  ([#977](https://github.com/alexisbeaulieu97/untaped/pull/977))
- **Breaking (plugins):** a line a plugin emits from a thread it started
  itself, without copying its context, no longer reaches the newest open run:
  it goes to the default text sink, ignores `--format`, `-q` and `-v`, and
  never counts toward the exit code. `bounded_map` and `Parallel.map` copy the
  context, as every thread untaped starts now does.
  ([#984](https://github.com/alexisbeaulieu97/untaped/pull/984))
- **Breaking (users):** the folders plugins keep follow the untaped home
  (`UNTAPED_HOME`) when their setting is unset: the repo store (`git.store_dir`)
  is `plugins/git/store/` under the home, workspaces
  (`workspace.workspaces_dir`) are under `plugins/workspace/checkouts/`, and
  dotfiles' clones, kept files and prompt files (`dotfiles.repos_dir`,
  `kept_dir`, `state_dir`) are under `plugins/dotfiles/clones/`, `kept/` and
  `prompt/`. Workspaces were under `~/.untaped/workspaces` and a prompt segment
  read `~/.untaped/plugins/dotfiles/attention`; set the setting, an absolute
  path, to keep a folder elsewhere.
  ([#996](https://github.com/alexisbeaulieu97/untaped/pull/996))
- **Breaking (scripts):** `github cache delete` and `cache prune` write
  `github.cache_delete_outcome` rows (`full_name`, `ref`, `action`,
  `disk_bytes`, `kept`, `path`): `action` is `deleted` (was `status: removed`),
  `released`, `planned` under `--dry-run` (was `status: cached`) or `failed`
  with `error`; a failed release used to print only a stderr line. A
  `github.corpus_repo` row no longer has `kept` or `detail`.
  ([#997](https://github.com/alexisbeaulieu97/untaped/pull/997))
- A profile the run picks (`--profile`, `UNTAPED_PROFILE`) that isn't defined
  reads `profile not found: 'x'; known: default, …` from every command,
  `profile current` included, still exiting 2.
  ([#997](https://github.com/alexisbeaulieu97/untaped/pull/997))
- `skills install` prints one row per skill it installs (`created`, `updated`
  when `--force` replaced one, or `failed` with `error`), of the kind `skills
  update` and `skills remove` print, instead of an `installed skill: NAME` line.
  `skills install --dry-run` and `setup register --dry-run` (with `--remove`
  too) print the `planned` row and write nothing.
  ([#998](https://github.com/alexisbeaulieu97/untaped/pull/998))
- `untaped doctor` checks git once, in `git.version`: the `github.git`,
  `ansible.git` and `workspace.git` rows that also said git was installed are
  gone. ([#999](https://github.com/alexisbeaulieu97/untaped/pull/999))
- `untaped doctor` marks its rows with theme symbols, so `untaped.ui.symbols`
  restyles them: `check.pass`, `check.warn`, `check.partial`, `check.fail`,
  `check.skip` and `check.fix` (the arrow before a fix). The `plain` theme
  keeps its ASCII marks; a theme with only an ASCII border no longer swaps
  them. ([#999](https://github.com/alexisbeaulieu97/untaped/pull/999))
- `untaped dotfiles apply` and `remove` show each row's `target_path` in
  tables, and the plan they ask about is the same table of planned rows, laid
  out as every other untaped preview; it names each target by its absolute
  path, where it used to write `~`. A row's `error` now comes after its
  `target_path` in JSON.
  ([#999](https://github.com/alexisbeaulieu97/untaped/pull/999))
- `untaped_github.api.GithubApi`'s batched ref probes run `concurrency`
  chunks at once and report progress through `on_progress(done, total)`. A
  chunk whose request fails for any reason but a GraphQL error gives its repos
  a `request` failure, with the error's `category`, instead of stopping the
  probe; a GraphQL error still stops it. `batch_default_branch_refs` asks for
  100 repos a request, not 200, and `rate_limit_remaining` is the lowest
  budget any response reported.
  ([#999](https://github.com/alexisbeaulieu97/untaped/pull/999))
- `untaped recipe`'s errors are no longer also `ValueError`s; code that catches
  one names its class or `RecipeError`.
  ([#999](https://github.com/alexisbeaulieu97/untaped/pull/999))
- Core's help, its examples and its agent skill no longer name plugins that may
  not be installed: they use core's own settings or a `PLUGIN`, `SKILL` or
  `SECTION` placeholder.
  ([#1000](https://github.com/alexisbeaulieu97/untaped/pull/1000))
- A plugin whose commands fail to build is reported as `bad-commands`, the
  reason `untaped doctor` shows, not `bad-app-factory`.
  ([#1001](https://github.com/alexisbeaulieu97/untaped/pull/1001))
- `untaped --help` and `untaped docs` open with what untaped is, in place of
  "Unified untaped developer CLI.", and `plugin rank` has examples.
  ([#1001](https://github.com/alexisbeaulieu97/untaped/pull/1001))
- **Breaking (plugin authors):** `untaped lint`'s `layer` rule checks what
  importing a plugin's light modules loads: `api.py`, `settings.py` and
  `records.py`, and the package `__init__` they run, never import `commands/`,
  directly or through another module (imports inside a function or an `if
  TYPE_CHECKING:` body don't count). It no longer checks a `cli → application →
  domain` direction: the plugin's own logic may import its commands. The
  `settings` rule still flags settings resolved in `domain/`, `application/` or
  `infrastructure/`. Both rules' findings now carry the import's line.
  ([#1002](https://github.com/alexisbeaulieu97/untaped/pull/1002))
- Errors name their system with `System` (`untaped`, `local`, `git`,
  `http`) or, for a plugin's own service, `Service(Settings)`, which follows
  the plugin's name: awx, github and jira errors read the name the plugin
  runs under, and so do the parts of a test run awx blames (`awx.suite`,
  `awx.credentials`, ...), whose hints and warnings spell awx's commands
  under that name too. `untaped lint`'s own errors now name `local`, not
  `lint`. ([#1005](https://github.com/alexisbeaulieu97/untaped/pull/1005))
- **Breaking (plugin authors):** `rejected_token_error` takes the plugin's
  `Service(Settings)`, not its section name, so the error and its `auth login`
  hint follow a renamed plugin.
  ([#1020](https://github.com/alexisbeaulieu97/untaped/pull/1020))
- **Breaking (plugin authors)**: `untaped lint`'s `example-command` rule also
  checks each command named in inline code in a command's docstring and its
  options' help, so a mention of a command that no longer exists fails the
  check; such a mention may leave out required arguments.
  ([#1025](https://github.com/alexisbeaulieu97/untaped/pull/1025))
- **Breaking (plugin authors)**: `derive(base, name, qualname=...)` binds a
  derived command where its module holds it (`PATCH = derive(Patch, "Patch",
  qualname="PATCH", ...)`), so each command derived from one base has its own
  `module:qualname`. Composition now refuses a plugin that mounts two different
  command classes at one target (`duplicate-target`): a hint spelling one of
  them named whichever was mounted first. awx's per-kind commands each have
  their own, so its hints and `untaped lint` fixes name the right kind.
  ([#1026](https://github.com/alexisbeaulieu97/untaped/pull/1026))
- The install instructions add `--compile-bytecode` to `uv tool install`, so
  the first command after an install no longer spends seconds compiling.
  ([#1039](https://github.com/alexisbeaulieu97/untaped/pull/1039))
- **Breaking (users):** a workspace is the git part of a project.
  `workspace create NAME` makes `<workspaces_dir>/NAME` a project (as
  `untaped project init` does, recording the run's `--profile` pick) and keeps
  the workspace's rows in its `.untaped/`; `workspace add` works in any project.
  The project holds each repo it checks out, so `untaped project remove`
  releases them, `project move` reconnects the worktrees, and git refuses to
  prune them (they are locked). `archive` removes the worktrees and keeps the
  folder and its rows; `create NAME` on an archived workspace resumes it,
  checking its repos out again. `remove NAME` lets go of the repos through the
  holds service (one refusal changes nothing), then drops the workspace's rows
  and the project when workspace made it. NAME is a workspace's name or its
  folder; two active workspaces with one name exit 2. `list` and
  `status --all` show the projects that are here now. `workspace remove` rows
  are `planned`, `refused`, `released`, `removed`, `kept` or `failed`, and lose
  `freed_bytes`.
  ([#1012](https://github.com/alexisbeaulieu97/untaped/pull/1012))
- **Breaking (plugins):** plugins declare floors on `untaped` and on other
  plugins (`untaped>=12.0`, `untaped-git>=12.1`), never an upper limit or an
  exact pin, so any number of plugins install together and keep working across
  releases. `lint.requirement-floor` replaces `lint.provides-requirement`; it
  fails an upper limit or a pin, except between packages built from one uv
  workspace. `untaped plugin new` scaffolds floors.
  ([#1050](https://github.com/alexisbeaulieu97/untaped/pull/1050))
- **Breaking (plugins):** before importing a plugin, untaped checks everything
  its distribution requires against what is installed: `untaped` and other
  plugins by version, any other package by presence. One that isn't met, or a
  required plugin that is disabled, disables that plugin alone
  (`unmet-requirement`) with the fix; installing a missing library is enough
  to bring it back. A plugin whose module raises on import is `import-failed`,
  not `malformed-entry-point`, and the `owner-out-of-range` contract reason is
  gone. Running a disabled plugin's command exits 4 with the reason instead of
  reporting an unknown command.
  ([#1050](https://github.com/alexisbeaulieu97/untaped/pull/1050))
- **Breaking (plugins):** a provider may take fewer parameters than its
  contract method: core binds each call to the contract's signature, applies
  its defaults and passes the provider only the parameters it declares, by
  name, so an owner can add an optional parameter without breaking providers
  released before it. A provider taking a parameter the contract doesn't
  declare, or one positional-only, is quarantined alone (`uses-missing`). The
  surface file is format 3: each optional parameter of a contract method has a
  line of its own, and a contract's methods are listed only as `contract`
  lines; `assert_surface` rewrites an older snapshot by itself.
  ([#1052](https://github.com/alexisbeaulieu97/untaped/pull/1052))
- Names plugins use (the SDK, `untaped.core.api`, each plugin's `api` and
  `testing` modules, contracts and record kind names) now last two majors after
  their deprecation, so a plugin built on 12.x keeps working on 13.x;
  `assert_surface(Plugin, version=...)` holds a plugin's own names to the same
  rules and drops a line from its snapshot once it is due, instead of asking you
  to delete the file.
  ([#1053](https://github.com/alexisbeaulieu97/untaped/pull/1053))
- `untaped doctor fix` lists the fixes it doesn't run under "Skipped", and a
  skipped fix says only what it needs (`needs <URL>`, `asks for input`).
  ([#1070](https://github.com/alexisbeaulieu97/untaped/pull/1070))
- The setting a `Dir(at=...)` places is home-only without a `HomeOnly()` mark,
  and a `Dir`'s `at=` must name one of its plugin's own settings.
  ([#1071](https://github.com/alexisbeaulieu97/untaped/pull/1071))
- **Breaking (users):** a setting only the home's base sets, such as
  `git.store_dir`, is no longer read from a profile: a run whose picked profile
  sets one exits 4, `config set --profile` refuses it, `profile create
  --copy-from` leaves it out, and doctor's `untaped.home-only` row names one in
  a profile nobody picks.
  ([#1071](https://github.com/alexisbeaulieu97/untaped/pull/1071))
- A SIGTERM or SIGHUP (`kill`, `timeout`, a closed terminal, a cancelled CI job)
  runs the same cleanup as Ctrl-C, such as cancelling the AWX jobs a test run
  launched, and stops untaped's child processes before untaped ends by that
  signal; it used to end untaped on the spot.
  ([#1074](https://github.com/alexisbeaulieu97/untaped/pull/1074))
- `untaped[awx]`, `untaped[dotfiles]`, `untaped[lint]` and `untaped[recipe]`
  now install the git plugin too, so its `git` commands, settings and doctor
  rows appear alongside them: those plugins run git through it.
  ([#1097](https://github.com/alexisbeaulieu97/untaped/pull/1097))
- Background processes a `workspace run` command starts keep running after it
  exits, unless they still hold its output: those are stopped two seconds after
  it exits. All of them used to be stopped, except ones in their own session.
  ([#1096](https://github.com/alexisbeaulieu97/untaped/pull/1096))
- Ctrl-C during `workspace run` sends the running commands SIGINT, as a
  terminal does, instead of SIGTERM, and SIGKILL two seconds later; a command
  that cleans up on Ctrl-C now gets to.
  ([#1096](https://github.com/alexisbeaulieu97/untaped/pull/1096))
- A `workspace run` command that times out gets SIGTERM, then SIGKILL two
  seconds later, so it can clean up; it used to get SIGKILL at once.
  ([#1096](https://github.com/alexisbeaulieu97/untaped/pull/1096))
- What a pack's hook leaves running in its worker's process group is stopped
  when the worker ends (SIGTERM, then SIGKILL two seconds later); a helper meant
  to outlive the run starts its own session.
  ([#1100](https://github.com/alexisbeaulieu97/untaped/pull/1100))
- `untaped recipe packs add` and `sync` fetch a git pack through the git
  plugin's repo store, so a private pack installs with the credentials a host's
  plugin supplies and untaped's proxy; a commit `--rev` must be one the
  remote's branches or tags reach, the pack is written exactly as committed,
  and `packs remove` releases the store repository once no installed pack uses
  its URL. ([#1102](https://github.com/alexisbeaulieu97/untaped/pull/1102))
- jira's `--dry-run` and confirmation previews name each change (`update
  ABC-1`, `assign ABC-1`, `transition ABC-1`) instead of an HTTP method and
  path. ([#1122](https://github.com/alexisbeaulieu97/untaped/pull/1122))
- `untaped awx` detects whether the server runs AWX or AAP, and which release,
  on first use, and talks to its controller API where that release serves it
  (behind the platform gateway from AAP 2.5). `awx.server_version` (`AWX
  24.6.1`, `AAP controller 4.6.2`) says it instead of detecting; `awx ping
  --refresh` detects again. A controller untaped-awx doesn't serve (AWX before
  18) exits 4, and `untaped doctor` gains the `awx.server-api` row.
  ([#1123](https://github.com/alexisbeaulieu97/untaped/pull/1123))
- **Breaking (users):** a recipe input's `sensitive:` is now `secret:`, with no
  old spelling, and `recipe get` rows name the field `secret`. A `list` input
  given as text (`--var`, `--vars-file` or a `default`) also takes
  comma-separated text (`--var cols=name,owner`), a missing required `list` is
  asked at a terminal as comma-separated text, and a run missing several
  required inputs names them all at once. Some messages are reworded: a path
  field refusing a secret reads `secret input 'x' cannot be used in field
  'dest'`, a structured value a template can't hold reads `structured input 'x'
  cannot be rendered into text`, and an unknown input error, from `--var` or
  `--input-from`, names every unknown input and the declared ones and exits 2
  (usage), as does an `--input-from` naming a global input or whose expression
  doesn't compile. `--var`, `--vars-file` and `--non-interactive` list under
  their own help panel, which says `NAME=VALUE` and links `untaped docs values
  guides vars`. ([#1127](https://github.com/alexisbeaulieu97/untaped/pull/1127))
- `awx test` suite variables use the `values` plugin, as recipe inputs do: a
  missing `--vars-file` fails when the flag is parsed (exit 2), a key no suite
  declares in a `--vars-file` is a usage error (exit 2, was 1) and reads
  `unknown input: NAME; declared: ...`, a `list` given as text starting with
  `[` is read as YAML, a `list` variable may set `items` to type its elements,
  a prompt reads `NAME for FILE (description)`, and `untaped docs values guides
  vars` says how values are given, parsed and asked. `awx test list` rows
  always carry a variable's `description`.
  ([#1128](https://github.com/alexisbeaulieu97/untaped/pull/1128))
- **Breaking (users, plugin authors):** the git plugin owns repo names: the
  `RepoSource` contract, its `Repo` record and the lookup that turns a typed
  name or URL into one repo moved from workspace to `untaped_git.api`, so a
  plugin can name repos without depending on workspace. Piped repo rows are
  `git.repo` (was `workspace.repo`), the answer cache's age is
  `git.caches.repo_source_repos.max_age_seconds`, and providers are ranked with
  `untaped plugin rank git.repo_source repos PLUGIN...`. Lookups, messages and
  exit codes are otherwise unchanged.
  ([#1132](https://github.com/alexisbeaulieu97/untaped/pull/1132))
- **Breaking (users):** `untaped dotfiles` reads packages: each
  `.untaped/dotfiles/<name>.yml` file of a source (a git repo or a local
  folder) is one package of named items, with its own format `version`,
  `{{ input }}` targets and per-OS and per-tag filters, checked when it loads;
  the `dotfiles.yml` manifest is no longer read. The machine's sources, the
  packages it turned on, its policies and its input answers live in
  `config.yml` under `dotfiles.sources`, so copying that file to a new
  machine and running `dotfiles apply` clones each source and places what is
  on. The commands are `subscribe`, `unsubscribe`, `show`, `add`, `remove`,
  `apply` and `status`, taking `PKG`, `PKG/ITEM`, `SOURCE/PKG` or
  `SOURCE/PKG/ITEM`; `items`, `enable`, `disable`, `repos`, `sync`, `summary`
  and `diff` are gone. A git source's clone is a worktree of the git plugin's
  repo store under `dotfiles.clones_dir` (it was `dotfiles.repos_dir`), so the
  git plugin's credentials and proxy apply. `apply` fetches and moves each
  clone itself. The records are `dotfiles.package`, `dotfiles.package_detail`,
  `dotfiles.entry_status` and `dotfiles.apply_outcome`.
  ([#1133](https://github.com/alexisbeaulieu97/untaped/pull/1133))
- **Breaking (users):** `untaped dotfiles apply` with no selector follows each
  entry's policy: `sync` places what is new or changed and takes back what
  the source dropped, `once` places a file once and leaves it, and `manual`
  places only what was just turned on; the rest waits for a person, and
  `apply` then exits 3 (a timer's `SuccessExitStatus=3`). Naming packages or
  items, or the new `--all`, places them whatever their policy. A `link` is a
  link only under `sync`: under `once` or `manual` it is a copy, and a link
  whose item stops following `sync` becomes a copy of what it shows before
  the clone moves. Two new states: `new` (the author added the entry after
  the item was first applied here) and `off` (placed for a package or item
  that is off now). The preview shows a diff wherever existing content
  changes, and a new file's size; each `dotfiles.apply_outcome` row carries
  the whole diff in a new `diff` field. `apply` and `add` take `--var` and
  `--vars-file`: a value answers every package in the run that declares the
  name and is kept in `config.yml`, and an input with no answer is asked at a
  terminal. `status --check` exits 3 when there is something for `apply` or
  a person to do, and `status.json` gains `apply`, `new` and `off` counts.
  ([#1137](https://github.com/alexisbeaulieu97/untaped/pull/1137))
- **Breaking (users):** `untaped ansible` names a repository as its provider
  lists it, so a nested name such as `group/sub/project` works in `deps`,
  `impact`, `find` and `graph`, by name, by URL and in `NAME@ref` root lines,
  and as a `source-alias set` target. The index keeps each repository's URL: a
  URL in a requirements file or on the command line names a repository when
  its host is one any refreshed source's repositories live on (a graph command
  also takes the hosts installed plugins serve), so a URL on any other host
  stays unresolved. A download URL (`.tar.gz`, `.zip`), a web
  page and a template stay unresolved. Source removal releases each repository
  at its stored URL. The index moves to layout 7 (`index.v7.sqlite3`), so a
  12.0 install refreshes its sources once.
  ([#1140](https://github.com/alexisbeaulieu97/untaped/pull/1140))
- `untaped lint`'s `import-boundary` rule lets a plugin import the
  experimental screens kit (`untaped.screen.core` and the modules of
  `untaped.screen.components`), outside the SDK's promise.
  ([#1146](https://github.com/alexisbeaulieu97/untaped/pull/1146))
- github's `RepoSource` waits for an inventory scope only to list repos: piped
  `github.repo` rows become `git.repo` rows with no scope and no token, and a
  selection needs only a token. Without a token github is no longer asked to
  list or select (before, it was asked and failed on the missing token): a name
  lookup says "github wasn't asked: no token" and how to log in. Doctor's
  provider row names the waiting methods (`repos waits for
  github.default_org`).
  ([#1148](https://github.com/alexisbeaulieu97/untaped/pull/1148))
- A plugin may fill one contract with several providers, one per record of its
  own, each asked, cached (its answer-cache key, as an `untaped.stale` line
  shows it, is now `<plugin>:<kind>:<hash>`) and checked on its own, and
  `Answers(pending=)` takes stale lines by answer position, while two providers
  issuing one record are still both refused.
  ([#1147](https://github.com/alexisbeaulieu97/untaped/pull/1147))
- **Breaking (users):** an `untaped ansible` source is the repos one plugin
  selects: `source set NAME --stdin` saves the selector a plugin prints
  (`untaped github repos selector --team acme/platform --format pipe`), and
  `source set NAME --from PLUGIN --match 'infra/*'` takes the repos a plugin
  lists whose full name matches. Each refresh asks the source's plugin for its
  repos again, at the URLs it gives, so `github.git_protocol` decides how
  GitHub repos are fetched. `source list` and `source get` rows carry `source`
  (the plugin), `selects` (the selector in words) and `selector` in place of
  `orgs`, `teams` and `repos`. `source set`, `source list` and `source get` are
  experimental while the git plugin's `RepoSelector` is.
  ([#1151](https://github.com/alexisbeaulieu97/untaped/pull/1151))
- **Breaking (users):** with no source selected, `untaped ansible deps`, `find`
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
  ([#1152](https://github.com/alexisbeaulieu97/untaped/pull/1152))
- `awx export --out-dir` names files with letters, digits and `._-` only (any
  other character becomes `_`), and a name over 128 bytes ends in a hash, so a
  long resource name no longer stops the export.
  ([#1156](https://github.com/alexisbeaulieu97/untaped/pull/1156))
- `untaped plugin list -f pipe | untaped lint --stdin` checks untaped itself as
  the root plugin too, instead of skipping its row.
  ([#1157](https://github.com/alexisbeaulieu97/untaped/pull/1157))

### Deprecated

- `DoctorCheck(online=True)` and the `DoctorCheck.online` attribute are
  deprecated and removed in untaped 14.0; use `DoctorCheck(network="online")`
  and `DoctorCheck.network`.
  ([#1116](https://github.com/alexisbeaulieu97/untaped/pull/1116))

### Removed

- **Breaking (plugins):** `# untaped: allow settings-naming` no longer waives
  a setting's name; `untaped lint` reports every field that breaks the
  settings naming rules.
  ([#837](https://github.com/alexisbeaulieu97/untaped/pull/837))
- **Breaking (plugins):** `untaped.sdk.testing.allows`, `allowed_names` and
  `import_targets` are gone; the `# untaped: allow` marker and the import
  resolver are untaped-lint's own.
  ([#837](https://github.com/alexisbeaulieu97/untaped/pull/837))
- `untaped plugin check`, deprecated in 11.1: `untaped lint` (from
  `untaped[lint]`) runs its file rules, and `check(install(...))` in a plugin's
  tests fails on its composition rows.
  ([#860](https://github.com/alexisbeaulieu97/untaped/pull/860))
- **Breaking (users):** `UNTAPED_CONFIG` and `UNTAPED_STATE` are gone:
  `config.yml` is always at the home's root and each plugin's state in its own
  folder, so a different config file means a different home (`UNTAPED_HOME`). A
  command run with either still set exits 4 naming `UNTAPED_HOME`, rather than
  writing to `~/.untaped`.
  ([#862](https://github.com/alexisbeaulieu97/untaped/pull/862))
- **Breaking (plugins):** `untaped.sdk.HttpSettings.retired_keys`: untaped's
  section starts clean at 12.0, so it retires no `http` key.
  ([#863](https://github.com/alexisbeaulieu97/untaped/pull/863))
- **Breaking (users):** `untaped.http.verify_ssl` and
  `untaped.http.verify_hostname`: turn certificate checks off for one server
  with its `verify_tls` (`untaped config set awx.server.verify_tls false`), or
  trust a CA file there. `config migrate` no longer moves tokens or TLS
  settings, and drops its `--yes`.
  ([#864](https://github.com/alexisbeaulieu97/untaped/pull/864))
- **Breaking (plugins):** a plugin that declares a credential no longer gets
  11.0's `<name>` and `<name>_command` settings in its section, so
  `Credential.get` reads none; `HttpSettings` loses `verify_ssl` and
  `verify_hostname`.
  ([#864](https://github.com/alexisbeaulieu97/untaped/pull/864))
- **Breaking (users):** `untaped profile use` and the `active:` key are gone:
  nothing in `config.yml` picks a profile. A run reads the profile `--profile`
  or `UNTAPED_PROFILE` names, else only the base; `profile current` reports
  `flag`, `env` or `fallback`.
  ([#865](https://github.com/alexisbeaulieu97/untaped/pull/865))
- Copy skills, deprecated in 11.1: `SkillAsset(source=...)` is gone, every
  skill is a stub written from core's template, and a plugin ships at most one.
  untaped no longer converts an 11.0 copy to a stub, writes or reads
  `backups/skills/`, or sees a `.untaped-skill.json` directory as its own. The
  `untaped.skill` and `untaped.installed_skill` records lose `kind` and
  `untaped.skill` loses `source`.
  ([#872](https://github.com/alexisbeaulieu97/untaped/pull/872))
- **Breaking (plugins):** `untaped_git.api` drops `adopt`, `Adopted`,
  `bare_repos`, `overlaps_store`, `remove_if_emptied`, `repo_origin` and
  `unfinished_removals`, which only the directory migrations used.
  ([#880](https://github.com/alexisbeaulieu97/untaped/pull/880))
- The experimental `copy_data_file` leaves `untaped.sdk`; nothing used it once
  the directory migrations went.
  ([#881](https://github.com/alexisbeaulieu97/untaped/pull/881))
- **Breaking (users):** ansible no longer renames 11.0's `index.sqlite3` to its
  layout's name; it builds its index anew, and leaves the old file in place.
  ([#881](https://github.com/alexisbeaulieu97/untaped/pull/881))
- A registered install no longer removes 11.0's `plugins/<name>/caches/`
  folders after a fill, and no `untaped.pruned` line without a store or layout
  is written: `store` and `layout` are always set. Delete a `caches/` folder
  11.0 left by hand.
  ([#881](https://github.com/alexisbeaulieu97/untaped/pull/881))
- **Breaking (users):** `untaped setup migrate-dirs` and doctor's
  `migrate-dirs` row are gone, with every row it ran: core's (the references
  of installs that are gone, an uninstalled plugin's `cached/` folder) and the
  plugins' (10.x's and 11.0's folders: ansible's index and git caches,
  dotfiles' directory, github's sweep cache, corpus and inventory, recipe's
  library, workspace's clones and `repositories`), and the store layouts the
  automatic cleanup keeps; doctor's `stores` row still names each.
  ([#881](https://github.com/alexisbeaulieu97/untaped/pull/881))
- **Breaking (plugins):** the directory migration hook is gone:
  `PluginSpec.migrations`, `DirMigration`, `MigrationOptions`, `MigrationRow`,
  `MigrationOutcome`, `delete_migration`, `move_migration`, `old_dirs`,
  `other_layouts_migration` and `unsafe_dir`.
  ([#881](https://github.com/alexisbeaulieu97/untaped/pull/881))
- **Breaking (users):** the setting `jira.base_url`, deprecated in 11.x for
  `jira.server.url`, is retired: it is no longer read, doctor names its
  successor, and `untaped config migrate` moves it there. The setting
  `jira.confirm` is retired too: jira asks before writes that replace or
  remove, and `--yes` skips the question; doctor names it and
  `untaped config migrate` deletes it. `jira issues create`, `jira issues
  comment` and `jira issues links create` only add, so they never ask and no
  longer take `--yes`; passing it exits 2. `--dry-run` still previews them.
  ([#914](https://github.com/alexisbeaulieu97/untaped/pull/914))
- **Breaking (plugins):** `fills.json`, the owner schema hash `assert_fills`
  wrote into it, the `plugin check` rows `fills` and `schema`, the
  `owner-schema-drift` reason and lint's `lint.fills`, `lint.schema` and
  `lint.owner-schema-drift` findings are gone. What guards a provider against an
  owner that moved is the owner floor in its `pyproject.toml`
  (`unmet-requirement`), validation of every answer as the owner's model, and
  the owner's own surface snapshot.
  ([#918](https://github.com/alexisbeaulieu97/untaped/pull/918))
- **Breaking (users):** `untaped agents` (experimental since 11.1) is removed;
  `untaped docs` replaces it. Agent skill stubs now send agents to `untaped
  docs`. ([#936](https://github.com/alexisbeaulieu97/untaped/pull/936))
- **Breaking (users):** `untaped auth set`, `auth unset` and `auth migrate`
  and their `untaped.auth_outcome` rows: `auth login` stores a token and `auth
  forget` deletes it. No plugin reads a token from `config.yml` any more, so
  doctor's plain-text token warning and `setup`'s tab that moved one to the
  store are gone, and `auth status` prints one row per credential.
  ([#951](https://github.com/alexisbeaulieu97/untaped/pull/951))
- **Breaking (plugins):** `untaped.sdk.TokenCommand`, `TokenSources` and
  `connection_check`; `connected_client`'s `required=`, `base_url_field=` and
  `bearer_token_field=` (pass `base_url=`, now required, and `bearer=`); the
  `untaped.plaintext-token`, `untaped.unmarked-service` and
  `untaped.connected-client-by-name` behaviours; and lint's
  `lint.unmarked-service` rule. A service marks what it needs with `NeedsSetup`
  and declares each secret as a `Credential`; a plugin that marks nothing has
  no `setup` row and its online check always probes.
  ([#951](https://github.com/alexisbeaulieu97/untaped/pull/951))
- **Breaking (users):** the setting `awx.base_url`, deprecated in 11.x for
  `awx.server.url`, is retired: it is no longer read, and doctor names its
  successor. ([#952](https://github.com/alexisbeaulieu97/untaped/pull/952))
- **Breaking (plugins):** the stderr helpers deprecated in 11.x for
  `ctx.diag` (`untaped.sdk.Diagnostics`): `UiContext.message`, `.success` and
  `.progress`, `report_error`, `report_row_errors`, and the `err=` parameter of
  `echo` and `UiContext.styled`. Also `untaped.sdk.project_root` (use
  `checkout_root`) and `untaped.sdk.testing.check_conventions` (use
  `untaped_lint.testing.assert_clean`).
  ([#952](https://github.com/alexisbeaulieu97/untaped/pull/952))
- **Breaking (plugins):** `untaped.sdk.StdinInput` and `read_stdin_input` are
  gone with the 11.x pipe reader (see contract-envelope for `PipeEnvelope` and
  `convert`). A command reads piped rows with a `Stdin[R]` field: `values()`,
  `rows()` or `identifiers()`, each row validated whole as its kind's model,
  strictly as JSON. `untaped.sdk.testing.piped_through(bridge, row)` reads a
  provider's row as the owner's `Stdin(via=bridge)` does.
  ([#953](https://github.com/alexisbeaulieu97/untaped/pull/953))
- **Breaking (plugins):** a record's `table_columns` class variable, and
  `untaped.sdk.records.table_columns_of`, are gone. A record marks its default
  table columns on its fields with `Column`. The rule that a listed record with
  more than four fields declares default columns is gone too: a record that
  marks none of its own fields shows every field.
  ([#955](https://github.com/alexisbeaulieu97/untaped/pull/955))
- **Breaking (plugins):** `untaped.sdk.read_identifiers` and
  `untaped.sdk.resolve_text_input` are gone. No command calls them since the
  moves to `Stdin[R]` and `TextOrStdin`.
  ([#957](https://github.com/alexisbeaulieu97/untaped/pull/957))
- **Breaking (plugins):** `untaped.sdk.get_config_section` and
  `untaped.sdk.report_errors` are gone. A 12.0 command reads its settings as
  `self.settings` and raises an `UntapedError` for core to print, so no plugin
  calls either. ([#958](https://github.com/alexisbeaulieu97/untaped/pull/958))
- **Breaking (plugins):** `untaped.sdk.UiContext` and `ui_context()` are gone.
  The terminal a command asks through is `self.ctx.terminal`, typed
  `untaped.sdk.Terminal`, a type with no constructor: the run makes it. It
  keeps the prompts (`confirm`, `text`, `secret`, `select`), `pick_many`, `run`,
  `require_screen_terminal` and the `terminal(refusal=)` guard. Its stdout
  methods (`collection`, `detail`, `styled`), `confirm_action`,
  `confirm_or_cancel` and `multiselect` are gone, and so is `print_tree`.
  `multiselect` also left the `PromptBackend` protocol, with
  `ScriptedPromptBackend(multiselects=)`, `ScriptedTerminal.multiselect` and
  `untaped.screen.prompts.multiselect_screen`. `tree_text` draws the same text
  whatever the theme: `tree_text(graph, ascii_guides=True)` replaces
  `ui=` with an ASCII theme.
  ([#960](https://github.com/alexisbeaulieu97/untaped/pull/960))
- **Breaking (plugin authors):** `untaped_lint.api.Subject`,
  `untaped_lint.testing.subject`, `@rule`'s `id=`, `reads=`, `summary=` and
  `renamed_from=`, and the `renamed_from` column of `lint.rule`.
  ([#962](https://github.com/alexisbeaulieu97/untaped/pull/962))
- **Breaking (plugins):** an entry point naming an 11.x `PluginSpec` no
  longer composes: it is quarantined (`not-a-plugin-class`, which replaces
  `not-a-spec`) with a line naming the class to name instead. Also, in
  `untaped.sdk.testing`, the `provides=` parameter of `compose_with` and the
  `PluginSpec`s that `compose_with`, `plugin_candidate` and `assert_surface`
  took; `plugin_candidate` takes a plugin class alone, without
  `distribution=` or `requires_dist=`.
  ([#974](https://github.com/alexisbeaulieu97/untaped/pull/974))
- **Breaking (plugins):** guide files: `PluginSpec.guides` and the guide
  pages read from its Markdown files, with the lint rules that checked them
  (`guide-name`, `guide-description`, `guide-heading`).
  ([#975](https://github.com/alexisbeaulieu97/untaped/pull/975))
- **Breaking (plugins):** 11.x shared state: `StateMap`, `StateCollection`,
  `PluginSpec(state=)`, the spelled `StateRows("acme", "items", model=...,
  key_field=..., remove_hint=...)` with its lazy `model=` function form,
  `StateRows.per_project("acme", ...)`, and the `settings-state-overlap` and
  `unlisted-state` quarantine reasons. `untaped doctor` drops its
  "load state file" row, and `config set` no longer names a state key as
  "managed by" its plugin: no state key sits in a settings section any more.
  ([#977](https://github.com/alexisbeaulieu97/untaped/pull/977))
- **Breaking (plugins):** `@writes` and the lint rules that plugin classes
  make moot: `missing-help`, `duplicate-option`, `empty-negative`,
  `needless-negative`, `positional-or-keyword`, `undeclared-write`,
  `mutation-format`, `destructive-controls`, `deprecated-option-panel` and
  `mark-on-spec`; `wrong-deprecated` now checks settings only. Composition
  checks what they did (every option has help, no flag answers twice, flags
  follow the default, and now an argument can't be deprecated:
  `deprecated-argument`), and `confirm: Confirm` gives a command `--yes` and
  `--dry-run`. The `bad-skill-asset` and `duplicate-skill` quarantine reasons
  are gone too: a plugin's skill is derived from its class.
  ([#978](https://github.com/alexisbeaulieu97/untaped/pull/978))
- **Breaking (plugins):** the cyclopts option and output helpers leave
  `untaped.sdk`: `create_app`, the option aliases (`FormatOption`,
  `TreeFormatOption`, `ColumnsOption`, `YesOption`, `DryRunOption`,
  `StdinOption`, `ParallelOption`, `LimitOption`), `echo`, `emit`,
  `emit_document`, `render_rows`, `check_columns`, `existing_file`,
  `parse_kv_pairs`, `parse_json_pairs` and `clamp_parallel`. A plugin
  class's fields declare what they did.
  ([#979](https://github.com/alexisbeaulieu97/untaped/pull/979))
- **Breaking (plugins):** a block no longer takes its plugin and name by hand:
  `Cache("acme", "motd", ...)`, `Credential("acme", "token", ...)` and
  `DoctorCheck("acme.check", ...)` are gone, and so are `Cache(store=)`,
  `Store`, `setting_guard`, `retired_values`, `refresh_option`,
  `executable_check` and `online_check`. Composition no longer reports
  `foreign-cache`, `duplicate-cache`, `foreign-store`, `duplicate-store`,
  `foreign-credential` or `duplicate-credential`: a plugin class's attributes
  name its blocks, so neither can happen.
  ([#980](https://github.com/alexisbeaulieu97/untaped/pull/980))
- **Breaking (plugins):** the 11.x plugin-spec names leave `untaped.sdk`:
  `PluginSpec`, `SkillAsset`, `plugin_dir`, `committed_path`, `AppContext`,
  `app_context`, `deprecated_alias`, `DEPRECATED_OPTIONS` and
  `deprecated_behaviour`, and `Project.plugin_dir(spec)` goes too. `untaped
  lint` drops its `foreign-section` rule, which read
  `app_context().section(...)`.
  ([#981](https://github.com/alexisbeaulieu97/untaped/pull/981))
- **Breaking (plugins):** a record or diagnostic no longer declares its kind
  with `class Widget(Record, kind="acme.widget")`, and `record_kinds`,
  `DuplicateKindError` and `ForeignKindError` leave `untaped.sdk.records`
  with the `foreign-kind` quarantine reason. Composition names every kind:
  `<plugin>.<snake_case of the class>`, from the plugin that prints, reads or
  lists the model.
  ([#982](https://github.com/alexisbeaulieu97/untaped/pull/982))
- **Breaking (plugins):** `Reason.owner_not_installed` (`owner-not-installed`)
  is gone from `untaped.sdk.contracts`. A plugin requires the owner of every
  contract it fills, so a fill whose owner is not composed is set aside
  silently, and `doctor` no longer shows a `contract-provider` row for it.
  ([#986](https://github.com/alexisbeaulieu97/untaped/pull/986))
- **Breaking (plugins):** the testing kit's `MemoryStore`, `memory_store()`
  and `current_memory_store()` are gone. Every test runs with a
  `MemoryCredentials`, which the `credentials` fixture hands over.
  ([#989](https://github.com/alexisbeaulieu97/untaped/pull/989))
- **Breaking (plugins):** the testing kit's `ScriptedPromptBackend` and
  `assert_destructive_contract` are gone. `ScriptedTerminal` is the one scripted
  terminal, and it lists what it asked in `asked` (no longer `calls`).
  ([#990](https://github.com/alexisbeaulieu97/untaped/pull/990))
- **Breaking (plugins):** `untaped.sdk.batch_apply`, `BatchOutcome`, `finish`
  and `resolve_each` are gone: they were 11.x's way to confirm a batch, report
  each item's failure and pick the exit code. A command's `Confirm`,
  `ctx.diag.failure`, failed rows, `Failed()` and `Matched()` do these now.
  ([#991](https://github.com/alexisbeaulieu97/untaped/pull/991))
- **Breaking:** what 12.0 still read from 11.x: `config migrate` no longer
  maps the setting names 11.x retired (`ansible.cache_dir`,
  `ansible.repo_cache_path`, `ansible.stale_after`, the `ansible.git_*` and
  `*_concurrency` settings, `awx.test_timeout`, `github.cache_dir`,
  `github.corpus_path`, `github.inventory.path`,
  `github.inventory.max_age_seconds`, `github.sweep.sync_concurrency`,
  `recipe.library_root`); data files 11.x wrote without an `untaped` stamp
  (a repo's private files, recipe backups, github corpus metadata, project
  markers) are unreadable; an ansible index from before its schema stamp is
  rebuilt; and a workspace's repo rows validate their name and URL like any
  repo. For plugins, the experimental `read_data_file` and
  `write_data_file` give way to `DataFile`, which reads stamped files only.
  ([#1005](https://github.com/alexisbeaulieu97/untaped/pull/1005))
- **Breaking (plugin authors):** `untaped.sdk.storage.code_version` is
  removed. `installed_code(*modules)` from `untaped.sdk` names the code a
  cache's value depends on instead.
  ([#1065](https://github.com/alexisbeaulieu97/untaped/pull/1065))
- **Breaking (scripts):** `untaped setup plan` rows replace `by` (`user` or
  `agent`) with `secret`, true when the step takes a secret; failure records
  drop `fix_by`, and a hint always prints as `hint:`, never `hint (your step):`.
  ([#1070](https://github.com/alexisbeaulieu97/untaped/pull/1070))
- **Breaking (plugins):** `UntapedError` and every SDK error drop `fix_by`
  (the attribute and the keyword), and `untaped.sdk.errors.fix_by()` and
  `FixBy` are gone; an error's hint names its step on its own.
  ([#1070](https://github.com/alexisbeaulieu97/untaped/pull/1070))
- **Breaking (plugins):** `untaped.sdk.git` and `untaped.sdk.testing.git` are
  gone: running git belongs to the git plugin, so `run_git`, `git_toplevel`,
  `GitResult` and `GitCommandError` come from `untaped_git.api`, and the
  repo-store fixtures from `untaped_git.testing`.
  ([#1097](https://github.com/alexisbeaulieu97/untaped/pull/1097))
- **Breaking (users):** the settings `jira.api_prefix` and `jira.agile_prefix`
  are retired: jira detects which API its server serves, and doctor names them.
  ([#1122](https://github.com/alexisbeaulieu97/untaped/pull/1122))
- **Breaking (users):** the setting `awx.api_prefix` is removed: untaped finds
  the controller API's prefix from the server's version. A context path stays
  in `awx.server.url`.
  ([#1123](https://github.com/alexisbeaulieu97/untaped/pull/1123))
- **Breaking (plugins):** `untaped_github.api.GithubClient` is gone: a plugin
  reaches GitHub through `github_api(settings)`, github's port (`GithubApi`),
  served for the server's version.
  ([#1120](https://github.com/alexisbeaulieu97/untaped/pull/1120))
- **Breaking (users):** `--backend` leaves `ansible source refresh`, `deps`,
  `impact`, `impact checkouts`, `impact uses`, `find` and `graph`, and the
  settings `ansible.source_refresh_backend`, `ansible.probe_parallel` and
  `ansible.source_refresh_rate_limit_floor` are deleted. A source refresh probes
  each repository's refs through the plugin that serves its host and reads with
  `git ls-remote` what that probe could not, in one warning grouped by reason.
  It pauses once a host's probe budget is spent; the pause error belongs to
  that host's plugin and its `retry_at` is when the budget resets.
  ([#1142](https://github.com/alexisbeaulieu97/untaped/pull/1142))
- **Breaking (users):** the inline sources leave `ansible deps`, `impact`,
  `impact uses`, `impact checkouts`, `find` and `graph` (`--org`, `--team`,
  `--repo`, `--path`, `--ref-kind`, `--ref-pattern`, `--ref-scan-default`): save
  a source and pass `--source`. `ansible source set` loses `--org`, `--team` and
  `--repo`, and `source patch` loses `--add-`, `--remove-` and `--clear-` of
  `org`, `team` and `repo`; it still changes paths and ref selectors.
  ([#1151](https://github.com/alexisbeaulieu97/untaped/pull/1151))
- **Breaking (users):** `untaped ansible deps`, `find` and `graph` lose
  `--live`: downstream is read live only when no source is selected.
  ([#1152](https://github.com/alexisbeaulieu97/untaped/pull/1152))

### Fixed

- A value attached to a short option, alone or ending a cluster of short flags
  (`-n5`, `-xn5`), is that value: `-n5` used to give `-5`.
  ([#886](https://github.com/alexisbeaulieu97/untaped/pull/886))
- Arguments reach the command as given. An empty argument (`""`) is no longer
  dropped: `--name ""` is an empty name, and `profile create ""` says the name
  is invalid rather than missing. Data after `--` that looks like a short
  cluster is no longer split: `-- -ab` gives `-ab`, not `-a` and `-b`, and
  `-- -n5` gives `-n5`.
  ([#886](https://github.com/alexisbeaulieu97/untaped/pull/886))
- A global option (`--profile`, `--verbose`, `--quiet`, `--deprecated`) given
  more than once works on every command instead of failing as an unknown
  option: `-v -v`, or `-vv` in one cluster, turns on verbose (`-qq` quiet), and
  `--profile a … --profile b` stacks both in order, `b` over `a`, as `--profile
  a,b` does. ([#886](https://github.com/alexisbeaulieu97/untaped/pull/886))
- A command group that runs a command on its own (`untaped setup`) shows that
  command's description on its `--help` and its `untaped docs` page again,
  after its own line.
  ([#930](https://github.com/alexisbeaulieu97/untaped/pull/930))
- A guide or page whose docstring holds a fenced code block prints the block
  as written, and the rest of the guide after it.
  ([#943](https://github.com/alexisbeaulieu97/untaped/pull/943))
- `--columns` on a record that passes a service's answer through
  (`extra="allow"`) is checked against the rows it prints, as a mapping row's
  are, instead of refusing a field the model does not declare.
  ([#947](https://github.com/alexisbeaulieu97/untaped/pull/947))
- With ansible installed, every untaped start, `--help` included, is faster:
  it no longer loads the github plugin's application code.
  ([#993](https://github.com/alexisbeaulieu97/untaped/pull/993))
- `awx jobs` refuses a job id that isn't a positive integer before asking AWX,
  naming the id once (`error: abc: not a job id (a positive integer)`); 0 and
  negative ids were sent to AWX. `awx jobs wait --timeout` refuses a negative
  value, as `awx launch --timeout` does, and its waits stop with the run.
  ([#995](https://github.com/alexisbeaulieu97/untaped/pull/995))
- An integer reference to an AWX resource that doesn't exist is reported as
  missing: `awx lint FILES` gives an `awx.missing-reference` finding (exit 3)
  instead of stopping, and `apply` and `get --by-id` say `Inventory not found
  (id=999)` instead of printing the raw URL.
  ([#995](https://github.com/alexisbeaulieu97/untaped/pull/995))
- A `config.yml` that isn't UTF-8 text, or is a directory, stops every command
  with a clear error (exit 4) instead of a traceback; a directory no longer
  reads as an empty config.
  ([#995](https://github.com/alexisbeaulieu97/untaped/pull/995))
- `untaped lint --select` (and `awx lint --select`) naming no installed rule is
  refused (exit 2); it only warned and passed with nothing reported, so a CI
  step pinning a misspelled rule never failed.
  ([#995](https://github.com/alexisbeaulieu97/untaped/pull/995))
- `untaped project remove`, `move` and `prune` naming a project this home
  doesn't record exit 1 (not found), as other lookups do, and a name says the
  known ones (`project not found: 'x'; known: …`); they exited 2 (usage).
  ([#995](https://github.com/alexisbeaulieu97/untaped/pull/995))
- `untaped recipe validate` and `recipe test` stop with the environment's error
  (exit 4 when `uv` is missing) instead of reporting every hook pack as failed
  (exit 1). ([#995](https://github.com/alexisbeaulieu97/untaped/pull/995))
- `untaped ansible source patch` flags that leave an invalid source (no org,
  team or repo left, a repo that isn't `owner/name`) exit 2, as `source set`'s
  do; they exited 1. A damaged source row reads `a source selects at least one
  org, team or repo` instead of naming `source set`'s flags.
  ([#997](https://github.com/alexisbeaulieu97/untaped/pull/997))
- `awx` reads, `delete` and `edit` say `No job templates found.` (the kind's
  own words) when the selection matches nothing, only in a table, so `-f json`
  prints `[]` with nothing on stderr; they said `No matching JobTemplate
  found.` in every format. An ambiguous lookup's advice is its `hint:`, and its
  messages follow the house style (lowercase, no period).
  ([#997](https://github.com/alexisbeaulieu97/untaped/pull/997))
- `untaped config set` names a refused key once (`invalid value for
  'awx.page_size': Input should be greater than 0`). In `config get`, `set` and
  `unset`, an unknown key reads `setting not found: 'awx.nope'; known: …` with
  the section's keys (or the sections) instead of every key on one line, and
  it, like a retired or deleted key, is category `not_found` (exit 1, as
  before) with `untaped config migrate` as its hint where one helps.
  ([#997](https://github.com/alexisbeaulieu97/untaped/pull/997))
- `github cache worktree` names the repo that isn't cached (`cached repo not
  found: 'acme/x'`), and `jira issues comment` with a blank body says `the
  comment's body is empty` as an invalid value rather than a setup error.
  ([#997](https://github.com/alexisbeaulieu97/untaped/pull/997))
- `untaped recipe` says every missing thing as other lookups do, the name
  quoted (`backup not found: 'zzz'; known: …`, `template not found:
  'template.txt'`), with the not-found exit: backups, recipe and pack recipe
  files, templates and copy sources, transform and content files, packs, pack
  sources, hook projects and hook modules. A missing asset in `recipe
  validate` was an invalid value. `recipe packs sync` names the command that
  fixes a pack without a usable recorded source as its `hint:`, not inside the
  error. ([#997](https://github.com/alexisbeaulieu97/untaped/pull/997))
- The hints for a missing token, for no credential store answering, for a stored
  credential that can't be read, and those `auth login`, `auth forget` and `auth
  list` give now say "run" and quote the command, as other hints do: run
  `untaped auth login PLUGIN`, run `untaped auth list`.
  ([#998](https://github.com/alexisbeaulieu97/untaped/pull/998))
- A command line untaped can't parse is said as its other errors are
  (lowercase, no closing period): `command not found: 'nosuch'; known: …`,
  `unexpected argument: 'b'`, `config get: KEY requires an argument`,
  `unknown option: --columsn; did you mean --columns?`. A stray token after
  `--` is `unexpected argument after --: '--dry-run'`; it read `Unknown
  option: --dry-run. Did you mean --dry-run?`. The exit stays 2.
  ([#998](https://github.com/alexisbeaulieu97/untaped/pull/998))
- When `untaped.sdk.bounded_map` stops on an error and its `on_abort` raises
  too, the original error is the one raised, as with `bounded_iter`.
  ([#999](https://github.com/alexisbeaulieu97/untaped/pull/999))
- Every place that reads a GitHub `owner/name` repo id agrees on what one is:
  exactly one slash, an owner that does not start with a dot, a name that is
  not `.` or `..`. `untaped ansible` read `acme/roles/web` as a repo in a
  dependency source and in a live read; it now leaves such a source unresolved.
  ([#999](https://github.com/alexisbeaulieu97/untaped/pull/999))
- `untaped auth login` and `untaped config set --stdin` refuse a piped value
  the same way: `no token received on stdin`, `stdin should hold the value on
  one line`, both as an invalid value (exit 1). `auth login` exited 2 and
  `config set` said `--stdin expects exactly one value`.
  ([#999](https://github.com/alexisbeaulieu97/untaped/pull/999))
- A plugin requirement whose environment marker can't be compared, such as
  `python_version ~= "3"`, quarantines that plugin as `bad-metadata` instead of
  crashing composition or `untaped lint` with a traceback.
  ([#1050](https://github.com/alexisbeaulieu97/untaped/pull/1050))
- A deprecated Python API a plugin uses while a command runs warns again: a
  deprecated SDK function, a marked parameter and a behaviour's `warn` each
  print their `untaped.deprecation` line once per run. Python hides
  deprecation warnings raised outside `__main__`, so none of them ever
  reached the terminal; untaped now shows its own while a command runs, and a
  filter you set that matches them (`-W`, `PYTHONWARNINGS`, a test
  harness's) still decides first. A deprecated function's
  `warnings.deprecated` names `category=UntapedDeprecationWarning`, which
  `untaped lint` checks (`lint.deprecation-text`), and a parameter's record
  `id` names its function's module, as its surface line does.
  ([#1059](https://github.com/alexisbeaulieu97/untaped/pull/1059))
- A process a `git` command leaves behind that still holds git's output open is
  stopped two seconds after git exits, instead of keeping untaped waiting until
  the command's timeout; one that holds nothing, such as a credential cache
  daemon, keeps running.
  ([#1074](https://github.com/alexisbeaulieu97/untaped/pull/1074))
- Ctrl-C, at a prompt too, stops a `git` command untaped runs even when it
  ignores the interrupt or was just starting; it could run on until its own
  timeout. ([#1074](https://github.com/alexisbeaulieu97/untaped/pull/1074))
- A SIGTERM or SIGHUP sent to untaped during `workspace run` (`kill`,
  `timeout`, a closed terminal, a cancelled CI job) stops the commands it is
  running; they used to run on after untaped ended.
  ([#1096](https://github.com/alexisbeaulieu97/untaped/pull/1096))
- `config set` on a map of entries writes only the values given, no longer
  every default the entries fill in.
  ([#1095](https://github.com/alexisbeaulieu97/untaped/pull/1095))
- When a tag or remote-tracking ref made by hand keeps a repo store repository
  after a release, the message names that ref instead of workspace.
  ([#1099](https://github.com/alexisbeaulieu97/untaped/pull/1099))
- A pack's hook worker now gets untaped's Ctrl-C, SIGTERM or SIGHUP as it
  arrives, with what its hooks started, and SIGKILL two seconds later, also
  under `--parallel`; before, it ran in its own session, out of the signal's
  reach, and a parallel run waited for the hook timeout.
  ([#1100](https://github.com/alexisbeaulieu97/untaped/pull/1100))
- Writing several settings at once (the setup screen) names the setting whose
  value was refused, no longer the first one written.
  ([#1101](https://github.com/alexisbeaulieu97/untaped/pull/1101))
- `untaped_git.api.ls_remote` maps an annotated tag asked for by its full
  name to its commit, as it already did for a tag a glob matched.
  ([#1102](https://github.com/alexisbeaulieu97/untaped/pull/1102))
- `config migrate` and a write's check of a setting's current value now use the
  spelling runs read, never a retired one.
  ([#1103](https://github.com/alexisbeaulieu97/untaped/pull/1103))
- Removing a setting that isn't set, through `config unset`, setup or a plugin's
  settings write, now removes only its retired spellings, so `config migrate`
  can't bring the value back, and reports them; with none, it reports the
  setting unchanged. A setting set to null counts as set, as runs read it.
  ([#1109](https://github.com/alexisbeaulieu97/untaped/pull/1109))
- `plugin rank` with no providers, and removing a ranking in setup, now leave
  the file alone when nothing is stored for the method and report it unchanged;
  removing a null or malformed value is reported as a change.
  ([#1112](https://github.com/alexisbeaulieu97/untaped/pull/1112))
- A config write that changes nothing in the file, such as setting a value a key
  already holds or removing one from an empty `name:` profile, now leaves the
  file untouched and reports unchanged, through `config set`, `config unset`,
  `plugin rank`, setup and plugin settings writes.
  ([#1115](https://github.com/alexisbeaulieu97/untaped/pull/1115))
- A plain `untaped setup plan` (without `--online`) no longer runs the
  services' online checks only to show them as skipped.
  ([#1116](https://github.com/alexisbeaulieu97/untaped/pull/1116))
- `untaped recipe apply --recipe` without a pack path, and `recipe hooks run`
  given a validate hook with `--file` or content, or a transform hook without
  `--file`, exit 2 (usage); they exited 1. A validate hook given `--diff` now
  names `--diff` in its refusal.
  ([#1130](https://github.com/alexisbeaulieu97/untaped/pull/1130))
- A `merge` source's empty mapping (`a: {}`, `a = {}` or a bare `[a]`) no
  longer takes over the target's keys under it: `untaped dotfiles remove`
  leaves them, and editing them doesn't mark the entry changed.
  ([#1143](https://github.com/alexisbeaulieu97/untaped/pull/1143))
- `untaped dotfiles` writes a YAML merge source's dates and timestamps as
  dates, not quoted strings, so the program reading the target sees a date.
  ([#1143](https://github.com/alexisbeaulieu97/untaped/pull/1143))
- Two ansible index refreshes at once wait for each other instead of failing
  with `database is locked`, and an index that stays busy is a retryable error
  (exit 5). The index uses SQLite's WAL mode, so keep `ansible.index_dir` on a
  local disk. ([#1156](https://github.com/alexisbeaulieu97/untaped/pull/1156))
- `awx export -f json` keeps an org-less record's `organization: null`, and
  `awx apply` reads the JSON list it prints, so `export -f json | apply -` no
  longer moves such a record into the default organization.
  ([#1156](https://github.com/alexisbeaulieu97/untaped/pull/1156))
- Two `github sweep` runs over one repo with different `--refs` no longer
  record refs the other one pruned.
  ([#1156](https://github.com/alexisbeaulieu97/untaped/pull/1156))
- Writes to `config.yml` and `state.yml` through a symlink and through the real
  file wait for each other: the lock lives in the home's `locks/`, keyed by the
  real path, and no `config.yml.lock` is left beside the file.
  ([#1156](https://github.com/alexisbeaulieu97/untaped/pull/1156))
- `untaped auth login` exits 4 (the environment needs fixing) instead of 5
  (retry later) when no token store, or the one `--store` names, is usable on
  this machine, or when gpg can't run for the `pass` store.
  ([#1157](https://github.com/alexisbeaulieu97/untaped/pull/1157))
- An unknown `--columns` name's list of valid columns, `--columns '?'` and the
  fields on a record's `untaped docs` page give the record's fields in the order
  its rows are written: an outcome row's own fields first, then `action` and
  `error`. ([#1157](https://github.com/alexisbeaulieu97/untaped/pull/1157))
- `--columns -name` drops a default column as its own word after `-c`; it
  needed `-c=-name` before.
  ([#1157](https://github.com/alexisbeaulieu97/untaped/pull/1157))
- An argument that takes a leading hyphen, such as `untaped workspace run`'s
  command, gets a short cluster like `-la` whole, as typed, instead of failing
  with an unknown option.
  ([#1157](https://github.com/alexisbeaulieu97/untaped/pull/1157))
- The testing kit's pytest plugin puts back the composition each test began
  with, so a plugin one test composed no longer comes back in a later test's
  settings registry when that test leaves a `kept_composition` block.
  ([#1155](https://github.com/alexisbeaulieu97/untaped/pull/1155))

### Security

- untaped needs `filelock` 4.2.1 or later (was 3.32.5 to below 4) and
  `pydantic-settings` 2.14.2 or later, the first releases without the published
  advisories on their locks and secrets folders.
  ([#1155](https://github.com/alexisbeaulieu97/untaped/pull/1155))
- Tables, list and detail views, messages, errors, relayed job and hook logs
  and `--verbose` lines show control characters escaped (`\x1b`), so a value
  someone else wrote, such as a ticket summary, a task name or a server's error
  text, can no longer move the cursor, retitle the terminal or write to the
  clipboard. A relayed log keeps its colours. `json`, `yaml`, `raw` and `pipe`
  output, and a log or document a command prints on stdout, keep values
  verbatim. ([#1155](https://github.com/alexisbeaulieu97/untaped/pull/1155))

## Older releases

Each earlier major has its own file: [11.x](changelog/11.x.md),
[10.x](changelog/10.x.md), [9.x](changelog/9.x.md), [8.x](changelog/8.x.md),
[7.x](changelog/7.x.md), [6.x](changelog/6.x.md), [5.x](changelog/5.x.md) and
[4.x](changelog/4.x.md).
