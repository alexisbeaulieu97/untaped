"""The package file: every rule checked when a package loads, and packages that follow them."""

from __future__ import annotations

import pytest

from untaped_dotfiles.errors import PackageError
from untaped_dotfiles.package import PackageFile, parse_package

_LINK = "      - {path: a, target: ~/a}\n"


def _items(body: str = _LINK, *, head: str = "") -> str:
    return f"version: 1\n{head}items:\n  x:\n    files:\n{body}"


def _step(step: str, *, head: str = "") -> str:
    return f"version: 1\n{head}items:\n  x:\n    steps:\n      - {step}\n"


#: ``(package text, words the refusal says)``: one case per rule.
REFUSED: dict[str, tuple[str, str]] = {
    "not a mapping": ("- a\n", "must hold a mapping with version and items"),
    "invalid yaml": ("version: [1\n", "invalid YAML"),
    "no version": ("items:\n  x:\n    files:\n" + _LINK, "version"),
    "an unknown version": (_items().replace("version: 1", "version: 0"), "not a package format"),
    "a newer version": (_items().replace("version: 1", "version: 2"), "upgrade untaped"),
    "an unknown key": (_items(head="colour: red\n"), "colour"),
    "no items": ("version: 1\nitems: {}\n", "items"),
    "a bad item name": (_items().replace("  x:", "  X:"), "item name 'X' must be lowercase"),
    "an empty item": ("version: 1\nitems:\n  x: {}\n", "at least one file, step or repo"),
    "a path outside the source": (
        _items("      - {path: ../a, target: ~/a}\n"),
        "must not contain '.' or '..' segments",
    ),
    "an absolute path": (
        _items("      - {path: /etc/a, target: ~/a}\n"),
        "relative to the source's root",
    ),
    "a relative target": (
        _items("      - {path: a, target: a}\n"),
        "must start with '~/' or '/'",
    ),
    "a target climbing up": (
        _items("      - {path: a, target: ~/../a}\n"),
        "must not contain '..' segments",
    ),
    "two files on one target": (
        _items("      - {path: a, target: ~/a}\n      - {path: b, target: ~/a}\n"),
        "is placed twice",
    ),
    "merge with no known format": (
        _items("      - {path: a, target: ~/a.toml, mode: merge}\n"),
        "needs a .json, .yml or .yaml extension or a format",
    ),
    "format without merge": (
        _items("      - {path: a, target: ~/a, format: json}\n"),
        "format applies to merge files only",
    ),
    "an unknown mode": (_items("      - {path: a, target: ~/a, mode: hard}\n"), "mode"),
    "an unknown os": (_items(head="os: [beos]\n"), "os"),
    "an unknown policy": (_items(head="policy: always\n"), "policy"),
    "a step with no check": (_step("{name: s, run: [x]}"), "needs a check"),
    "a step with nothing to do": (
        _step("{name: s, check: {program: x}}"),
        "needs run, script or fix",
    ),
    "a step with run and script": (
        _step("{name: s, check: {program: x}, run: [x], script: x.sh}"),
        "has run and script",
    ),
    "args without a script": (
        _step("{name: s, check: {program: x}, run: [x], args: [y]}"),
        "args go with a script",
    ),
    "with without a kind": (
        _step("{name: s, check: {program: x}, fix: do it, with: {a: 1}}"),
        "with applies to a kind only",
    ),
    "a check with two tests": (
        _step("{name: s, check: {program: x, path: ~/x}, fix: do it}"),
        "exactly one of command, path or program",
    ),
    "prints without a command": (
        _step("{name: s, check: {program: x, prints: y}, fix: do it}"),
        "prints applies to a command check only",
    ),
    "a script outside the source": (
        _step("{name: s, check: {program: x}, script: ../x.sh}"),
        "must not contain '.' or '..' segments",
    ),
    "two steps of one name": (
        "version: 1\nitems:\n  x:\n    steps:\n"
        "      - {name: s, check: {program: x}, fix: a}\n"
        "      - {name: s, check: {program: y}, fix: b}\n",
        "step 's' is named twice",
    ),
    "a bad step name": (_step("{name: S, check: {program: x}, fix: a}"), "step name 'S'"),
    "stdin naming no input": (
        _step("{name: s, check: {program: x}, run: [x], stdin: token}"),
        "stdin names no declared input 'token'",
    ),
    "a placeholder naming no input": (
        _items("      - {path: a, target: '~/{{ dir }}/a'}\n"),
        "{{ dir }} in target names no declared input",
    ),
    "a secret in a command line": (
        _step(
            "{name: s, check: {program: x}, run: [x, '{{ token }}']}",
            head="inputs:\n  token: {secret: true}\n",
        ),
        "secret input 'token' cannot be used in run",
    ),
    "a secret in a target": (
        _items(
            "      - {path: a, target: '~/{{ token }}'}\n",
            head="inputs:\n  token: {secret: true}\n",
        ),
        "secret input 'token' cannot be used in target",
    ),
    "a secret with a default": (
        _items(head="inputs:\n  token: {secret: true, default: x}\n"),
        "a secret input has no default",
    ),
    "an input declared twice": (
        _step(
            "{name: s, check: {program: x}, run: [x], inputs: {email: {}}}",
            head="inputs:\n  email: {}\n",
        ),
        "input 'email' is declared twice",
    ),
    "a bad input name": (_items(head="inputs:\n  Email: {}\n"), "input name 'Email'"),
    "a list input": (_items(head="inputs:\n  names: {type: list}\n"), "type"),
}


@pytest.mark.parametrize("case", list(REFUSED))
def test_a_package_breaking_a_rule_is_refused_with_why(case: str) -> None:
    text, says = REFUSED[case]
    with pytest.raises(PackageError) as refused:
        parse_package(text, where="dots/x")
    assert str(refused.value).startswith("package dots/x")
    assert says in str(refused.value)


def test_a_check_path_starts_at_home_or_root() -> None:
    with pytest.raises(PackageError, match="check path must start with"):
        parse_package(_step("{name: s, check: {path: x}, fix: a}"), where="dots/x")


def test_one_path_may_go_to_several_targets() -> None:
    text = _items("      - {path: a, target: ~/a}\n      - {path: a, target: ~/b}\n")
    assert len(parse_package(text, where="dots/x").items["x"].files) == 2


def test_a_step_input_renders_in_its_own_step_and_a_kind_needs_no_check() -> None:
    package = parse_package(
        "version: 1\nitems:\n  x:\n    steps:\n"
        "      - {name: s, check: {path: '~/{{ dir }}'}, run: [x], inputs: {dir: {}}}\n"
        "      - {name: t, kind: brew, with: {formula: fish}}\n",
        where="dots/x",
    )
    assert [step.name for step in package.items["x"].steps] == ["s", "t"]


def test_merge_reads_the_format_from_the_target_or_format() -> None:
    package = parse_package(
        _items(
            "      - {path: a, target: ~/a.json, mode: merge}\n"
            "      - {path: b, target: ~/b.yaml, mode: merge}\n"
            "      - {path: c, target: ~/c.conf, mode: merge, format: yaml}\n"
        ),
        where="dots/x",
    )
    assert [f.merge_format for f in package.items["x"].files] == ["json", "yaml", "yaml"]


# -- the design's worked example, as package files (example.com stands in for the hosts) --

EXAMPLES: dict[str, str] = {
    "fish": """\
version: 1
description: Fish shell config and the plugin list (fisher installs the plugins)
policy: manual
os: [macos, linux]
items:
  config:
    files:
      - {path: fish/config.fish, target: ~/.config/fish/config.fish}
      - {path: fish/fish_plugins, target: ~/.config/fish/fish_plugins}
      - {path: fish/conf.d, target: ~/.config/fish/conf.d}
  plugins:
    steps:
      - name: fisher
        check: {program: fisher}
        fix: Install fisher, then run `fisher update` in fish.
""",
    "starship": """\
version: 1
description: Starship prompt
policy: sync
items:
  config:
    files:
      - {path: starship/starship.toml, target: ~/.config/starship.toml}
""",
    "git": """\
version: 1
description: Global gitignore and identity
policy: sync
inputs:
  git_name: {description: Name on commits}
  git_email: {description: Email on commits}
items:
  ignore:
    files:
      - {path: git/ignore, target: ~/.config/git/ignore}
  identity:
    steps:
      - name: user-name
        check: {command: [git, config, --global, user.name], prints: "{{ git_name }}"}
        run: [git, config, --global, user.name, "{{ git_name }}"]
      - name: user-email
        check: {command: [git, config, --global, user.email], prints: "{{ git_email }}"}
        run: [git, config, --global, user.email, "{{ git_email }}"]
""",
    "claude": """\
version: 1
description: Claude Code preferences, marketplaces and enabled plugins
inputs:
  claude_dir: {description: Claude Code's folder, default: "~/.claude"}
items:
  settings:
    files:
      - {path: claude/settings.json, target: "{{ claude_dir }}/settings.json", mode: merge}
""",
    "agent-instructions": """\
version: 1
description: One set of instructions for every coding agent
items:
  claude:
    files:
      - {path: agents/AGENTS.md, target: ~/.claude/CLAUDE.md}
  codex:
    files:
      - {path: agents/AGENTS.md, target: ~/.codex/AGENTS.md}
""",
    "agent-secrets": """\
version: 1
description: Runs one command with the secrets of one set
os: [windows]
items:
  scripts:
    files:
      - {path: agent-secrets/with-secrets.ps1, target: ~/.config/agents/with-secrets.ps1}
      - {path: agent-secrets/secrets, target: ~/.config/agents/secrets}
  vault:
    steps:
      - name: service-token
        description: The vault's service account token, encrypted for this user
        check: {path: ~/.config/agents/token.dpapi}
        inputs:
          service_token: {description: Service account token, secret: true}
        run: [pwsh, -NoProfile, -NonInteractive, -Command, "[Console]::In.ReadToEnd()"]
        stdin: service_token
""",
    "skills": """\
version: 1
description: Agent skills, for two harnesses
policy: sync
items:
  skills:
    files:
      - {path: .claude/skills, target: ~/.claude/skills}
      - {path: .claude/skills, target: ~/.agents/skills}
""",
    "projects": """\
version: 1
description: The repositories I work in, cloned where my tools expect them
items:
  tools:
    repos:
      - {url: https://git.example.com/acme/tools.git, target: ~/Projects/tools}
""",
}


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_the_worked_example_loads(name: str) -> None:
    package = parse_package(EXAMPLES[name], where=f"dots/{name}")
    assert isinstance(package, PackageFile) and package.items
