# Contributing

Thanks for contributing to `untaped`.

## Local setup

Install, test, lint and type-check with the commands in
[AGENTS.md](AGENTS.md#development-workflow).

## Documentation and capabilities

Update the owning concept page under `docs/` when a change affects the root
shell, capability composition, configuration, profiles, themes, output, stdin,
HTTP/TLS helpers, or agent workflows. Keep capability-specific command and
settings guidance with the capability that owns it, and link to the core pages
for shared mechanics.

Capability skills follow the [skill template](docs/templates/SKILL.md).

## Sensitive data

Do not include secrets, real customer configurations, production logs, private
workspace data, health exports, or other private data in issues, tests, fixtures,
or examples. Use synthetic data for tests and examples.
