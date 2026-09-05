"""Use case: emit a shell-specific snippet for ``uwcd <workspace>`` integration."""

from __future__ import annotations

from typing import Literal

from untaped_workspace.errors import WorkspaceError

Shell = Literal["zsh", "bash", "fish"]

_POSIX = """\
uwcd() {
  local p
  p="$(untaped workspace path "$1")" || return $?
  cd "$p"
}
"""

_ZSH = (
    _POSIX
    + """\
_uwcd_complete() {
  local -a workspaces
  workspaces=("${(@f)$(untaped workspace list --format raw --columns name 2>/dev/null)}")
  _describe 'workspace' workspaces
}
compdef _uwcd_complete uwcd
"""
)

_BASH = (
    _POSIX
    + """\
_uwcd_complete() {
  local cur names
  cur="${COMP_WORDS[COMP_CWORD]}"
  names="$(untaped workspace list --format raw --columns name 2>/dev/null)" || return 0
  COMPREPLY=($(compgen -W "$names" -- "$cur"))
}
complete -F _uwcd_complete uwcd
"""
)

_FISH = """\
function uwcd
    set -l p (untaped workspace path $argv[1]); or return $status
    cd $p
end

function __uwcd_workspaces
    untaped workspace list --format raw --columns name 2>/dev/null
end
complete -c uwcd -f -a '(__uwcd_workspaces)'
"""


class ShellInit:
    def __call__(self, shell: str) -> str:
        normalised = shell.lower().strip()
        if normalised == "zsh":
            return _ZSH
        if normalised == "bash":
            return _BASH
        if normalised == "sh":
            return _POSIX
        if normalised == "fish":
            return _FISH
        raise WorkspaceError(f"unsupported shell: {shell!r}; supported: zsh, bash, fish")
