A project's `.untaped/config.yml` (experimental) is a settings layer: a run
inside the project reads its `profile:` (a name or a list) and its values,
above the base and beneath `UNTAPED_PROFILE`, `--profile` and `UNTAPED_*`
variables, so a command's pick stacks on the folder's. The file counts only
when it and its folder are yours and nobody else can write them; a secret, a
command or a setting only the home holds there exits 4, and an unknown
profile name is skipped with a warning. `config set|unset --project` write it,
`config get|list` name it as `project:<name>`, `profile rename` and `delete`
rewrite the projects that pick the profile, `project init` records the run's
`--profile` or `UNTAPED_PROFILE` pick as the new project's `profile:`, and a
missing credential's hint names the run's pick (`untaped --profile <x> auth
login <plugin>`).
