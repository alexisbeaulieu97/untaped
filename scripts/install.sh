#!/usr/bin/env sh
set -eu

usage() {
  cat <<'EOF'
Usage: scripts/install.sh [--editable] [CORE_SPEC]

Install untaped into its managed virtual environment and write the user shim.

CORE_SPEC defaults to "untaped". With --editable, CORE_SPEC defaults to ".".
EOF
}

editable=0
core_spec="untaped"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ "${1:-}" = "--editable" ]; then
  editable=1
  shift
  if [ $# -gt 0 ]; then
    core_spec="$1"
    shift
  else
    core_spec="."
  fi
elif [ $# -gt 0 ]; then
  core_spec="$1"
  shift
fi

if [ $# -gt 0 ]; then
  usage >&2
  exit 2
fi

if [ -d "$core_spec" ]; then
  core_spec="$(cd "$core_spec" && pwd)"
fi

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
venv="$data_home/untaped/venv"
shim="$HOME/.local/bin/untaped"
requirements="$(mktemp)"
resolved="$(mktemp)"
trap 'rm -f "$requirements" "$resolved"' EXIT

if [ "$editable" -eq 1 ]; then
  uv run python -m untaped.installer "$core_spec" \
    --editable \
    --sync \
    --requirements "$requirements" \
    --resolved "$resolved" \
    --venv "$venv" \
    --shim "$shim"
else
  uv run python -m untaped.installer "$core_spec" \
    --sync \
    --requirements "$requirements" \
    --resolved "$resolved" \
    --venv "$venv" \
    --shim "$shim"
fi

printf 'installed untaped: %s\n' "$shim"
