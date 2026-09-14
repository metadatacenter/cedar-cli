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
    # Checked before activating: activate is a plain shell script and still sources cleanly after
    # the interpreter it was built against has been upgraded away, so sourcing first turns a
    # recoverable environment into a silent exit.
    if [ ! -x .venv/bin/python3 ]; then
      if [ -d .venv ]; then
        echo "The Python in $CEDAR_HOME/cedar-cli/.venv is gone; an interpreter upgrade leaves" >&2
        echo "its python3 link pointing at nothing. Rebuild the environment:" >&2
      else
        echo "There is no Python environment at $CEDAR_HOME/cedar-cli/.venv. Create it:" >&2
      fi
      echo "    cd $CEDAR_HOME/cedar-cli" >&2
      echo "    rm -rf .venv && python3 -m venv .venv" >&2
      echo "    .venv/bin/python3 -m pip install -r requirements.txt" >&2
      exit 1
    fi
    if ! source .venv/bin/activate; then
      echo "$CEDAR_HOME/cedar-cli/.venv/bin/activate could not be sourced; rebuild the" >&2
      echo "environment with the three commands in the cedar-cli README." >&2
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
