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

The main implementation areas are:

- `cedar.py` registers the top-level command groups.
- `org/metadatacenter/*.py` defines the Typer command surfaces.
- `org/metadatacenter/config/` and `org/metadatacenter/model/` describe repositories, images,
  targets, and plans.
- `org/metadatacenter/planner/`, `org/metadatacenter/executor/`, and
  `org/metadatacenter/worker/` translate commands into work and run it.
- `org/metadatacenter/util/` contains shared environment, mode, build-train, Docker, and process
  safeguards.
- `tests/` exercises command paths without starting a real CEDAR deployment.
- `cli.sh` is the shell wrapper the `cedarcli` alias sources, and the only one. It activates the
  repository virtual environment, preserves the caller's working directory for `build this` and
  `publish this`, and returns the Python process's exit status.

## Contributor Setup

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
