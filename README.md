# cedarcli

[![CI](https://github.com/metadatacenter/cedar-cli/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/metadatacenter/cedar-cli/actions/workflows/ci.yml)

Start with the published documentation:

- [cedarcli Manual](https://metadatacenter.readthedocs.io/en/latest/developer-guide/cedarcli/)
  explains repositories, Maven, builds, publication, and the native, hybrid, and Docker modes.
- [Developer Install](https://metadatacenter.readthedocs.io/en/latest/install-developer/overview/)
  installs cedarcli as part of a source development environment.
- [Docker Install](https://metadatacenter.readthedocs.io/en/latest/install-docker/overview/)
  installs cedarcli as part of a container deployment.
- [CEDAR CLI Cheat Sheet](https://metadatacenter.readthedocs.io/en/latest/install-docker/cedarcli-cheat-sheet/)
  is the compact command reference.

This README is for contributors to the CLI itself. The published manual is the user guide.

## What This Repository Implements

cedarcli is a Python command-line coordinator for a multi-repository CEDAR installation. It invokes
Git, Maven, npm, Docker Compose, and the native process controllers while preserving CEDAR's
dependency order and deployment-mode boundaries. It is not a daemon and does not replace those
underlying tools.

Backend test builds also guard their process boundary. `cedarcli test status` inventories orphaned
embedded MongoDB processes, and `cedarcli test cleanup` terminates only `mongod` executables under
`.embedmongo`; ordinary CLI and release Maven test tasks enforce the same check before and after
they run.

The main implementation areas are:

- `cedar.py` registers the top-level command groups.
- `org/metadatacenter/*.py` defines the Typer command surfaces.
- `org/metadatacenter/release_support/` owns release planning, state, package comparison,
  workspaces, version stamping, validation, Git integration, publication, preflight, and
  presentation in separate modules. `release_train.py` registers commands and explicitly
  re-exports the existing component API; components never import that command facade.
- `org/metadatacenter/docker_support/` separates engine calls, deployment state, images,
  setup, lifecycle, health checks, and status reporting. `train_support/` separates source
  surveys, GitHub workflow access, preflight, dispatch, and reporting. The existing worker
  classes delegate to these components with their original call signatures.
- `org/metadatacenter/config/` and `org/metadatacenter/model/` describe repositories, images,
  targets, and plans.
- `org/metadatacenter/planner/`, `org/metadatacenter/executor/`, and
  `org/metadatacenter/worker/` translate commands into work and run it.
- `org/metadatacenter/util/` contains shared environment, mode, build-train, Docker, and process
  safeguards.
- `tests/` exercises command paths without starting a real CEDAR deployment.
- `cli.sh` is the shell wrapper the `cedarcli` alias sources, and the only one. It activates the
  repository virtual environment in a subshell, preserves the caller's working directory for
  `build this` and `publish this`, and returns the Python process's exit status without exiting
  the caller's shell. Setup failures stop before Python runs; only successful commands may
  consume a pending Git navigation record.

Release `start`, `resume`, and `abandon` hold an exclusive process lock in the release state
directory for the whole operation, including preflight and retries. A competing modifying command
refuses immediately; `release status --watch` remains available. The OS releases ownership when
the command exits, including after a crash. The persistent `release.lock` file must not be deleted
while a release command is running.

Estate-wide Git commands visit every selected repository and return a nonzero exit status if
any repository fails. Their result records retain each repository's process exit code, including
failures that produce no stderr. A failed status scan does not update `git next` navigation.

Shared streamed subprocess execution lives in `util/ProcessRunner.py`: `run_process` takes
literal arguments, and `run_shell` takes one explicit script. Both return output lines with a
`returncode`. Worker script lists run in order and stop at the first failed script; shell state
is local to each script. Output preserves indentation and replaces invalid UTF-8 bytes. An
interrupted reader kills its owned process group, reaps the child, and closes the pipe.

Each root CLI invocation binds an `InvocationContext` containing its environment, settings,
repository/server catalogs, and task registries. Profile resolution updates that environment;
subprocesses receive it explicitly. `create_app()` registers commands without host inspection,
and importing `cedar` does not bootstrap a profile. `GlobalContext`, `Util.cedar_home`, and
`CedarCliSettings` remain compatibility accessors rather than owners of mutable process state.
Library callers can use `with use_context(InvocationContext(environment=...)):` or supply a
context as the root application's `obj`. Calls made outside a bound context retain ambient
library behavior. Change settings on `context.settings`, rather than assigning class attributes.

## Contributor Setup

Python 3.10 is the minimum supported version (including Ubuntu 22.04 deployment hosts).
CI tests Python 3.10 and 3.12. Git checkout supports Git 2.34.1; deployment does not
require replacing the system Python or adding a newer Git package repository.

The installation guides establish `CEDAR_HOME`, clone the companion repositories, and create the
normal alias. For work on cedarcli itself, create its isolated Python environment and install the
runtime and test dependencies:

```bash
cd "$CEDAR_HOME/cedar-cli"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt pytest
```

Run the test suite from this repository:

```bash
python -m pytest tests -v
```

The tests expect `cedar-development` and `cedar-docker-build` beside `cedar-cli`, matching the
normal `$CEDAR_HOME` layout. They mock external process and network work or use inspection-only
controller paths; the CI suite does not start CEDAR.

## Changing Commands

Keep command parsing in the command module and put orchestration in a planner or worker. Reuse the
repository catalog and shared target models instead of restating repository, service, frontend, or
image inventories. Mode checks belong at the command boundary so a new operation cannot silently
cross from native ownership into Docker ownership, or the reverse.

When a user-facing command changes, update its tests, the
[cedarcli Manual](https://github.com/metadatacenter/cedar-mkdocs/tree/main/docs/developer-guide/cedarcli),
and the
[cheat-sheet generator](https://github.com/metadatacenter/cedar-mkdocs/blob/main/tools/generate_cedarcli_cheatsheet.py).
Regenerate `assets/docs/cedar-cli.pdf` and `assets/docs/cedar-cli.png` rather than editing those
artifacts directly.
