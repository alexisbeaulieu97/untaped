# Configuration

Settings live in profiles in `config.yml`; data a plugin manages itself
lives in its own `plugins/<name>/state.yml`. The [configuration reference](./reference/config.md)
lists every setting, its default and its environment variable. Plugin
settings live in their own sections (`github.default_org`, `awx.server.url`);
their tokens live outside the file ([Tokens](#tokens)).

## File and layout

```text
~/.untaped/                       # the home; $UNTAPED_HOME moves it
  config.yml                      # settings and profiles
  plugins/<name>/                 # each plugin's own files
    state.yml                     # its state
```

untaped keeps everything in one folder, the home: `~/.untaped`, or the
absolute path in `UNTAPED_HOME` (`~` is expanded; with a relative one, a command
reading the home exits 4, `--help` still prints). Moving the home moves config,
state, logins, core's files and the folders plugins keep under it, so a second
home shares none of them. A folder setting you set (`git.store_dir`) stays put.

### Renamed settings

A renamed setting keeps working under its old name until the major its
warning names, with a warning naming the new one. `untaped config migrate` renames
old keys in every profile of `config.yml` (`--dry-run` first shows what it
would change); it does not touch environment variables, so rename an
`UNTAPED_*` variable yourself. After that major an old key is
retired: it is no longer read, `untaped doctor` names it, and `config migrate`
still renames it. A setting with no new name is deleted instead: doctor says
why and what to set in its place, and `config migrate` removes the key,
printing the value it held so you can carry it over. The [configuration reference](./reference/config.md#renamed-settings)
lists every renamed setting.

Two installs that share one config file can disagree about a renamed setting:
writes keep keys a version does not know, but an install older than the
rename ignores the new key and uses the default. Run `config migrate` once every install
is upgraded, or give the second install its own `UNTAPED_HOME`; `untaped doctor` reports keys the running version ignores, and
names the other installs [registered](#several-versions-on-one-machine) here.

### File format

A top-level `format_version` names the file's on-disk format. `config.yml`
is format 2 and every write to it starts the file with `format_version: 2`. A
non-empty `config.yml` without the key, or with `format_version: 1`, was
written by untaped 11.x and is refused (exit 4): 12.0 has no upgrade path, so
delete it and run `untaped setup`, or keep untaped 11.1 for that home.
A `state.yml` at the top of the home was written by 11.x too, and is refused
the same way: delete it, or keep untaped 11.1 for that home. A file with a
newer format, or a `format_version` that is not a positive integer, is refused
on read and on write (exit 4): upgrade untaped instead of letting an older
release misread it. `config edit` refuses to open while `config.yml` has a
newer format, but opens a config with an invalid `format_version`, or one
written by 11.x, so you can fix it; the edit is saved only as format 2.

`config` and `profile` commands write only `config.yml`; a plugin's state
writes touch only its own `state.yml`.

The top level of `config.yml` is the base: one section per plugin, read by
every run. Named profiles go under `profiles:` and each one overlays the base.

```yaml
format_version: 2

untaped:
  http:
    ca_bundle_path: ~/certs/corp-ca.pem
  ui:
    theme: default
    border: rounded
    detail_view: list
awx:
  server: {url: https://aap.example.com}
github:
  default_org: acme
jira:
  server: {url: https://jira.example.com}
  default_project: OPS
  default_board_id: 42
workspace:
  workspaces_dir: ~/work/workspaces
  branch_template: "{name}"

profiles:
  prod:
    awx:
      server: {url: https://aap.prod.example.com, verify_tls: ~/certs/aap-prod.pem}
```

A plugin's `state.yml` holds its rows, outside profiles, and a stamp naming
the release that last wrote it ([Plugin state](./state.md)), as
`plugins/workspace/state.yml` does:

```yaml
untaped:
  file: 1
  by: 12.0.0
workspaces:
  - name: PROJ-123
    created_at: '2026-10-01T09:00:00Z'
    repos:
      - url: https://github.com/acme/api.git
        name: acme/api
        dir: api
        branch: PROJ-123
        base: main
```

Things that surprise people:

- A top-level section no installed plugin reads (a misspelt name, or a
  plugin you uninstalled) is ignored; `untaped doctor` flags it in its
  `untaped.unknown-keys` row. `active:` and `profiles.default` are 11.x keys and
  are refused: a run picks its profiles with `--profile` or `UNTAPED_PROFILE`,
  and the base is the top level.
- Each section is validated only when a command reads it. An invalid
  `awx.page_size: abc` breaks `untaped awx ...` but not `untaped github ...`;
  the error names the section and the file. `untaped doctor` and
  `untaped config list` still report every invalid section.
- The document root, `profiles` and each named profile must be mappings;
  anything else, or an unreadable file, is a configuration error naming the
  file. An empty entry (`prod:` with nothing under it) is an empty profile.

Config and state files are written atomically, owner-only (`0600`) and under a
lock: `config.yml` waits for it as long as `UNTAPED_CONFIG_LOCK_TIMEOUT` says
([Environment variables](./reference/environment.md)), a plugin's `state.yml`
up to 10 seconds. A symlinked file stays a symlink. Writes rewrite only the keys they change and keep your comments, key
order, quoting and indentation.

A setting's value comes from the first of these that has it: its
`UNTAPED_<SECTION>__<FIELD>` variable (for example `UNTAPED_AWX__SERVER__URL`),
the picked profiles (the last first), the base, the built-in default.

## Several versions on one machine

Installs of different untaped versions (a tool, a development checkout, a
one-off `uvx` run) can share one `~/.untaped`. Settings and state are read by
all of them; data a plugin can rebuild, such as the ansible dependency index,
is kept per layout so no install opens a file another one wrote in a shape it
cannot read (see [Stores](./caches.md#stores)).

Run `untaped setup register` once from each install you keep. It records the
install and the layouts it uses in `~/.untaped/plugins/untaped/refs/`, and from
then on a registered install removes the layouts no registered install holds,
after its next complete refresh, with an info line for each. `untaped setup
plan` lists registration as its first step (`--check` does not count it), and
the first row of the `untaped setup` screen registers this install. Before
removing an install, run `untaped setup register --remove` from it, so the
others may clean up after it; the reference of one deleted without it, not
reachable, counts for nothing, and `setup prune` removes it, with the folders
of plugins nothing here uses. `untaped doctor` lists every registered install
and, for each store, the layouts on disk and what holds them.

`untaped config migrate` (with or without `--dry-run`) and `doctor`'s `untaped.unknown-keys`
row name the other registered installs, since an older install may still read the keys a
migration would rewrite: migrate once every install is upgraded.

## Profiles

A profile is a named set of settings under `profiles:`. `default` names the
base, the top level of `config.yml`. A run layers the profiles it picks over
the base, in order, field by field: a later profile wins, mappings merge and
lists replace.

```bash
untaped profile create stage --copy-from default
untaped --profile stage config set awx.server.url https://aap.stage.example.com
export UNTAPED_PROFILE=stage
untaped --profile stage,lab-proxy awx jobs list
```

A run picks the profiles `UNTAPED_PROFILE` names, then the ones the global
`--profile` names, each a comma list (`--profile` may also repeat). With
neither, it reads only the base. `--profile default` reads the base alone and
sets `UNTAPED_PROFILE` aside; `default` in a longer list, a name that is not
letters, digits, `_`, `.` and `-`, or a profile not under `profiles:` exits 2.
Nothing in `config.yml` picks a profile: set `UNTAPED_PROFILE` in your shell
for a lasting choice.

`--profile`, `--verbose`/`-v`, `--quiet`/`-q` and `--deprecated` are global
and go anywhere before `--` (`untaped github --profile work whoami`).

- The base always exists and cannot be created, renamed or deleted; clear its
  keys with `config unset`. Other profiles must exist before a write targets
  them, and `profile create` and `rename` take only names `--profile` can
  pick.
- `profile create`, `delete` and `rename` accept `--dry-run`, which checks the
  change and writes nothing. `profile delete` asks first; pass `--yes` when
  not interactive.
- `profile list` marks the profiles the run reads (`selected`): the picked
  ones, or the base when nothing is picked.
- `profile show` prints YAML (its settings under `data`), secrets `***`
  unless `--show-secrets`; with no name it shows what the run reads.
- `profile current` writes only the pick, comma-joined (or `default`), to
  stdout and where it came from (`flag`, `env` or `fallback`) to stderr, so
  it is safe in a prompt; `--format json` lists each profile with its source:

  ```bash
  echo "[$(untaped profile current 2>/dev/null)] $ "
  ```

## Settings

Keys are always fully qualified (`awx.server.url`, never `server.url`),
untaped's own too (`untaped.http.*`, `untaped.ui.*`, `untaped.skills.*`).

```bash
untaped config list --all-profiles
untaped config get awx.server.url
untaped config set untaped.http.timeout_seconds 60 --dry-run
untaped --profile prod config unset jira.default_project
untaped config set untaped.ui.symbols '{"success": "✓", "error": "✗"}'
untaped config edit
```

Each cache a plugin keeps from an upstream has a setting
`<plugin>.caches.<cache>.max_age_seconds`: how long its data is used before
untaped checks upstream again (`0` checks every time). The
[config reference](./reference/config.md) lists them.

`config list` prints stable settings first, then experimental ones under an
`Experimental` heading. A deprecated setting appears under `Deprecated` once
it is set, as does any setting set under an old name, with a note naming the
release that removes it; `untaped --deprecated config list` lists them all,
and `config set` warns when it sets a deprecated setting. `--format json`
lists every setting with its `stability`, and a deprecated one with its
`removed_in` and `distribution`.

`config set` validates the value against the setting's type instead of
parsing it as YAML:

- string and secret settings store the input verbatim (`p4ss #word`,
  `0123456` and `no` stay as typed);
- mapping and list settings take the whole value as JSON or YAML;
- for an optional non-string setting, the literal `null` stores an explicit
  null; `config unset` removes the key instead;
- an invalid value is rejected before anything is written.

`config set` and `config unset` write to the one profile `--profile` names,
else to the base; `UNTAPED_PROFILE` picks what runs read, never where they
write, and `--profile a,b` is refused (exit 2). When a profile the run picks
still hides a value written to the base, one line says so. Each prints an
`untaped.setting_outcome` record that never echoes the value.

`config get` prints only the value (nothing when unset). Structured output
adds its source (`profile:<name>`, `base`, `env`, `default` or `unset`),
profile and default, with secrets masked as `"***"` unless
you pass `--show-secrets`.

Because a command validates only the section it reads, you can repair a
broken key through the CLI: `config set KEY --prompt` offers the raw stored
value as the default.

`config edit` opens `VISUAL`, falling back to `EDITOR`; include your GUI
editor's wait option (`EDITOR="code --wait"`). It validates the result before
saving. If the result is invalid, the file changed while you edited, or the
save fails, `config.yml` is left as it was and the error names the copy that
holds your edits. If the editor itself exits with an error, the edits are not
checked: copy the file over `config.yml` yourself and run `untaped doctor`.

`untaped doctor` checks offline, one row per check: the config file, each
plugin's state, the selected profile, every section, unknown keys, renamed, retired or
deprecated keys in any profile, a `pass` token command that gpg cannot serve
here, installed skills, a `setup` row per service (`not set up`, what is
still missing, or where its token comes from) and each plugin's own checks.
`--online` also authenticates against each service that is set up. A failed check makes it exit
nonzero; a `warn` row does not. To browse and write a profile's settings
interactively, its services' URLs and tokens included, see
[Getting started](./getting-started.md#set-up-your-services): `untaped setup`
writes each setting through the same code as `config set`.

## TLS and shared UI settings

`untaped.http.*` and `untaped.ui.*` apply to every plugin. HTTP
clients use the operating system trust store by default. To trust a corporate
CA (a proxy that inspects TLS, say) for every client and for git, set a CA
bundle once:

```bash
untaped config set untaped.http.ca_bundle_path /path/to/corp-ca.pem
```

One server's own certificate check belongs to that server: its
`verify_tls` is `true` (the trust above), a CA file for that server alone,
or `false` (no check; keep it for a controlled network). It is part of what a
stored token is filed under, so a token is never sent to a server checked
another way than when it was stored:

```bash
untaped --profile work config set awx.server.verify_tls /path/to/aap-ca.pem
```

untaped's own settings sit in its `untaped` section: `untaped.ui.theme` picks a built-in
theme and `untaped.ui.format` the default `--format`; the
[configuration reference](./reference/config.md#untaped) lists each one,
including the names `untaped.ui.symbols` and `untaped.ui.color_roles`
accept. `config set` and `doctor` reject any other name; a stray name already
in `config.yml` keeps working.

## Tokens

Tokens never live in `config.yml`: agents read that file to learn a setup,
people paste it into chats, and dotfile tools copy it. A token is filed for
one server (its URL and how its certificate is checked) with `untaped auth
login`, in this machine's password store or as a command that prints it:

```bash
untaped auth login awx                       # the server this run uses; prompts
pass show work/jira | untaped auth login jira    # from stdin
untaped auth login github --token-command 'gh auth token'
untaped auth list                            # every stored login
untaped auth status                          # which source answers, for this run
untaped auth forget awx                      # or: untaped auth forget --id 3f9c…
```

`auth login` prints the server first, then reads the token from a hidden
prompt or stdin, never from an argument. Without a plugin on a terminal it
offers a menu of what this run needs; off a terminal the plugin is required.
It uses the first store that works here: macOS `security` (the login
keychain), `secret-tool` (a Secret Service such as GNOME Keyring), then `pass`
(GPG, which works over SSH with gpg-agent; usually what a headless server or
WSL2 ends up with; it needs an initialised store, a gpg secret key for it and a
working pinentry, with `GPG_TTY` set in a terminal). `--store` picks one.
Before asking for a token it tests the store with a throwaway value (`pass`: a
gpg encrypt and decrypt, run with your `PASSWORD_STORE_GPG_OPTS`;
`secret-tool`: a store and read back) and stops with the cause if that fails.
It hands the token to the store on stdin and reads it back before filing it.
Run it again to replace a rotated token. The index of logins (never the
tokens) is kept in core's `plugins/untaped/state.yml`; entries are
`<plugin>/<name>/<id>` under the `untaped` service, prefixed by a hash of the
config file's path for a home other than `~/.untaped`, so two homes never
share one.

A plugin's token for a server comes from the first of these that has one:

1. `UNTAPED_<PLUGIN>__TOKEN` (`UNTAPED_<PLUGIN>__<NAME>` for a secret not
   named `token`), an override for this process;
2. the login stored for exactly this server;
3. the service's conventional environment variables, in the order the
   [environment table](./reference/environment.md#untaped) lists them
   (`GH_TOKEN`, `JIRA_API_TOKEN`, `CONTROLLER_OAUTH_TOKEN`, …).

With none, a command exits 4 naming `untaped auth login <plugin>`: logging in
and setting a token variable are the person's steps, never an agent's.

A token command (`--token-command`) is an argv list, run without a shell, at
most once per process and only when a command first needs the token. It runs
in the home (`~/.untaped` by default) rather than the folder
you are in, so a repository can't plant the program it finds; give a file the
command reads as an absolute path. `untaped` strips surrounding whitespace
from the output and never prints the token, the command's arguments or its
output: a failure reads `error: the stored jira token (3f9c…): 'op' exited
with status 1` (or `not found on PATH`, `printed no token`, `timed out after
60s`). `untaped` never runs `gh auth token` on its own; log in with
`--token-command 'gh auth token'` to reuse the GitHub CLI's login.

A store over SSH can fail on an unlock prompt nobody sees: unlock it first
(`security unlock-keychain` on macOS). Store commands time out after 60s.
When neither stdin nor stderr is a terminal (an agent running `untaped`,
cron), `pass` is never asked for gpg's passphrase: a locked key fails at once.
For a plain `pass show <entry>`, the error names the command that unlocks it
from your terminal; gpg-agent then keeps the passphrase cached for a while. A
pinentry mode you set yourself, in `PASSWORD_STORE_GPG_OPTS` or `gpg.conf`, is
left alone: loopback with a passphrase file for cron, or
`--pinentry-mode=ask` to let an agent's command open a desktop pinentry you
can answer. If an earlier run left a passphrase prompt open on another
screen, `gpgconf --kill gpg-agent` closes it (it also forgets cached
passphrases, SSH key unlocks included).

What this protects: the token is no longer in a file read casually, and it
is only ever sent to the server it was filed for. It does not stop a program
running as you, which can run the store's own command. Agents in particular
should be denied reads of `~/.untaped/` and the password store in their
harness.

### Tokens from untaped 11.0

untaped 12.0 reads no token from `config.yml`: log in again with `untaped
auth login <plugin>`.

## Debug logs

`--verbose`/`-v` sends debug logs to stderr through Python's `logging`, as
`DEBUG untaped.<area>: <message>` lines:

- `untaped.http`: each request's method, URL, outcome and time, each retry
  wait and redirect, and the `untaped.http.proxy` a service's client uses;
- `untaped.git`: each `git` command's arguments (URL credentials masked), its
  directory, exit status and time, and `[auth header]` when a token was
  passed (the header itself never appears);
- `untaped.auth`: which token source was used (never the token);
- `untaped.sdk.contracts`: the traceback when a plugin's provider or contract
  raises an unexpected exception (its answer or doctor row shows only
  `KeyError: …`; JSON Lines carry the message only), and why an answer was
  not cached.

Every line masks a URL's user name and password and an `Authorization`
value. `--verbose` is the only switch; there is no log-level setting.
