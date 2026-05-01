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

_FISH = """\
function uwcd
    set -l p (untaped workspace path $argv[1]); or return $status
    cd $p
end
"""


class ShellInit:
    def __call__(self, shell: str) -> str:
        normalised = shell.lower().strip()
        if normalised in ("zsh", "bash", "sh"):
            return _POSIX
        if normalised == "fish":
            return _FISH
        raise WorkspaceError(f"unsupported shell: {shell!r}; supported: zsh, bash, fish")
