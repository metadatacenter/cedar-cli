#!/bin/bash
# Keep setup local to the invocation; sourcing this file must never exit the caller.
_cedarcli_run() {
  local cedar_cli_cwd="$PWD" cedar_cli_rc next_git_path
  local NEXT_GIT_FILE=$HOME/.cedar/next_git_repo
  if [ -z "${CEDAR_HOME:-}" ]; then
    echo 'CEDAR_HOME must name the CEDAR installation directory.' >&2
    return 1
  fi
  if (
    cd "$CEDAR_HOME/cedar-cli" || exit 1
    source .venv/bin/activate || exit 1
    if [ ! -x .venv/bin/python3 ]; then
      echo 'The cedar-cli virtual environment has no executable python3.' >&2
      exit 1
    fi
    if { [ "${1:-}" = build ] || [ "${1:-}" = publish ]; } && [ "${2:-}" = this ]; then
      .venv/bin/python3 "$CEDAR_HOME/cedar-cli/cedar.py" "$@" --wd="$cedar_cli_cwd"
    else
      .venv/bin/python3 "$CEDAR_HOME/cedar-cli/cedar.py" "$@"
    fi
  ); then
    cedar_cli_rc=0
  else
    cedar_cli_rc=$?
  fi
  if [ "$cedar_cli_rc" -eq 0 ] && [ -f "$NEXT_GIT_FILE" ]; then
    next_git_path=$(cat "$NEXT_GIT_FILE") || return 1
    cd "$next_git_path" || return 1
    rm "$NEXT_GIT_FILE" || return 1
  fi
  return "$cedar_cli_rc"
}
_cedarcli_run "$@"
