A command that stops because a service setting such as `awx.base_url` is
missing now names the profile it looked in and the other profiles that set
it, with the `--profile` option or `UNTAPED_PROFILE` value that uses one, so
an agent can rerun it there instead of asking you for a value you already
configured.
