#!/bin/bash
CEDAR_CLI_CWD=$PWD
pushd $CEDAR_HOME/cedar-cli > /dev/null
source .venv/bin/activate;
if [ "$1" = 'build' ] && [ "$2" = 'this' ]; then
  python "$CEDAR_HOME/cedar-cli/cedar.py" "$@" --wd="$CEDAR_CLI_CWD"
elif [ "$1" = 'deploy' ] && [ "$2" = 'this' ]; then
  python "$CEDAR_HOME/cedar-cli/cedar.py" "$@" --wd="$CEDAR_CLI_CWD"
else
  python "$CEDAR_HOME/cedar-cli/cedar.py" "$@"
fi
# Capture the CLI's exit code before popd / next-git handling clobber $?.
# This file is *sourced* by the `cedarcli` alias, so its exit status is that of
# its last command; without this, python's failure code was discarded and every
# invocation returned 0 (a failed `build java` looked green). See below.
CEDAR_CLI_RC=$?
popd > /dev/null
NEXT_GIT_FILE=$HOME/.cedar/next_git_repo
if test -f "$NEXT_GIT_FILE"; then
  #echo "$NEXT_GIT_FILE exists. Sourcing..."
  cd $(cat "$NEXT_GIT_FILE")
  rm "$NEXT_GIT_FILE"
fi
# Propagate the captured code. `return` sets the status when sourced (the normal
# path) without terminating the interactive shell; the `|| exit` fallback covers
# the case where this file is executed directly rather than sourced.
return $CEDAR_CLI_RC 2>/dev/null || exit $CEDAR_CLI_RC
