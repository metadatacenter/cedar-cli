# cedar-cli

[![CI](https://github.com/metadatacenter/cedar-cli/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/metadatacenter/cedar-cli/actions/workflows/ci.yml)

## About
CEDAR CLI is CEDAR's command line interface used to facilitate:
* Development
* Docker install
* Native install
* Managing a running CEDAR server

As such, you should install `cedar-cli` in the context of an existing or a new `CEDAR` installation.

This is why we are setting `CEDAR_HOME` and the alias in the script below. You should have these set in your bash profile. 
## How to install

```bash
export CEDAR_HOME='/Users/cedar-dev/CEDAR/'

cd ${CEDAR_HOME}
git clone https://github.com/metadatacenter/cedar-cli

cd cedar-cli
git checkout develop

python -m venv ./.venv
source .venv/bin/activate
pip install -r requirements.txt

alias cedarcli='source $CEDAR_HOME/cedar-cli/cli.sh'

cedar.py --help
```

## Available commands
`cedar-cli` is executed by running `cedarcli` after the alias is set.

The available commands will be listed by executing:
```bash
cedarcli
```

## Native and Docker deployments

Select one persistent deployment mode before using either command surface. Selection validates the
required profiles and starts nothing. It is deliberately a one-time operation: stop the current
deployment and run `cedarcli mode --clear` before selecting a different mode.
The CLI checks the actual runtime as well as the saved choice: native operations stop before touching
a recorded or running Compose deployment, Docker starts reject verified native applications and
host infrastructure listeners, and hybrid Docker starts permit native frontends but reject native
backend processes. It also detects a leftover Docker frontend project in hybrid mode.

Clearing a mode does not abandon processes that only that mode is expected to manage. Native mode
must have its applications and infrastructure stopped first; hybrid must have its native frontends
and Docker projects stopped; and Docker must have its Docker projects stopped. Stop commands on the
selected command surface remain available when saved state and the runtime disagree, so those
conflicts can be cleaned up safely.

If Docker has deliberately been shut down before `cedarcli docker stop all`, the daemon cannot
confirm teardown. `cedarcli mode --clear --force` discards the inactive Docker deployment record in
that recovery case. It does not stop containers and refuses to bypass Compose projects while the
daemon is running.

```bash
cedarcli mode native        # complete host-based stack
cedarcli mode hybrid        # native frontends with Docker backend
cedarcli mode docker        # complete container stack
```

Native and Docker deployments deliberately have separate status commands. Docker keeps some ports
private to `cedarnet`, so native host-port probes cannot assess a container deployment accurately.

```bash
cedarcli native status
cedarcli docker status
```

Native starts are headless. `cedarcli native start all`, `native start microservices`, and
`native start frontends` use the shared process controller; they do not open iTerm or Terminal. The
fifteen Java services and seven frontends write separate logs under `$CEDAR_HOME/log/` and PID files under
`$CEDAR_HOME/log/run/`. A PID file or occupied port is never enough to authorize a signal: the
controller first verifies the expected CEDAR jar or frontend source directory and refuses foreign
owners.

```bash
cedarcli native start all
cedarcli native health
cedarcli native logs resource
cedarcli native restart resource
cedarcli native stop all
```

`cedarcli native status` reports both managed application processes and the broader native
host/port inventory, including infrastructure. Use `cedarcli native watch` for a continuously
refreshing application view.

The aggregate Docker start command uses the configured topology and waits for it to become ready:

```bash
cedarcli mode docker
cedarcli docker start all
```

- `docker` starts and checks all 29 core containers.
- `hybrid` starts the 22-container backend and routes Docker nginx to seven native frontend servers;
  run `cedarcli native start frontends` before the Docker aggregate.

`cedarcli docker status` applies the configured container and route expectations without another
option. Administration containers are managed separately with the `admin` target. Native mode
rejects Docker operations, Docker mode rejects native operations, and hybrid rejects native backend
operations and Docker frontend starts. Stop is the deliberate exception: the selected command
surface can stop stale components even when the runtime is inconsistent, and hybrid can stop
leftover Docker frontends.

Starts default to `--pull never`, which uses local images and fails if one is absent. Use
`--pull missing` to fetch only absent images or `--pull always` to refresh every image from its
configured registry. After preflight passes, `--timeout` bounds the ordered startup, health wait,
authentication-route probe, and frontend-route checks.

```bash
cedarcli docker start all --pull never --timeout 600
cedarcli docker stop all
```

Individual `start` and `stop` commands remain available for troubleshooting. When an aggregate mode
is active, recreating infrastructure through those commands preserves its nginx routing. Starting
the Docker frontend project while `hybrid` is active is refused. Stopping that project is allowed so
an accidental or leftover full-Docker frontend tier can be removed before continuing.

## Immutable development build trains

`cedarcli publish train` dispatches the central publication workflow in `cedar-development`. It
derives the development base version from `cedar-parent`, allocates a UTC train ID, publishes the
ordered Java artifact set, then builds and publishes the complete 31-image Docker estate. Resume is
the only selector exposed to the operator:

```bash
cedarcli publish train
cedarcli publish train --resume <TRAIN>
```

Resume uses the source manifest recorded before publication; it does not rebuild the current heads
of `develop`. A Maven completion pointer advances after all Java artifacts exist. A separate Docker
pointer advances only after all 31 registry images have been pulled back and their provenance and
digests verified. Docker builds resolve the Maven pointer; Docker starts resolve the deployable
Docker pointer. Either accepts `--train <TRAIN>`. Use `--local` on both build and start for
checked-out Java artifacts and the legacy development image tag.

## Cheat sheet
The full set of commands and subcommands will be shown as a `pdf` file after executing:
```bash
cedarcli cheat
```

![CEDAR CLI commands](assets/docs/cedar-cli.png?raw=true "CEDAR CLI commands")

## Split frontend publication and native deployment

Workspace and Template Designer remain outside `release all-in-one` until migration acceptance.
They are also excluded from the generic `publish frontends` and `publish all` commands, so a
generic publication cannot include or activate them accidentally.

Use the explicit commands when preparing a split-frontend build:

```bash
# Reproducibly install the exact locked dependencies in both Git checkouts.
cedarcli build split-frontends

# On a native staging/production host, configure both static trees and write build identity.
# This requires CEDAR_FRONTEND_BEHAVIOR=server and the exact environment frontend URLs.
cedarcli build split-frontends --server-payload

# Publish both current package versions to the Nexus registries declared by their package.json files.
cedarcli publish split-frontends --dry-run
cedarcli publish split-frontends

# Run or stop both development Gulp servers locally on ports 4201 and 4202.
cedarcli native start frontend split-frontends
cedarcli native stop frontend split-frontends
```

`publish` runs `npm ci` and `npm publish` in each repository. It does not change DNS, certificates,
nginx, Keycloak, CORS, or public routing. Those environment operations remain separate acceptance
and cutover gates.

Staging and production need no Docker. Their nginx virtual hosts serve
`cedar-workspace/app` and `cedar-template-designer/app` directly after `--server-payload` exits, just
as the existing native deployment serves the monolith's `app` tree. The start/stop commands are for
the local `CEDAR_FRONTEND_BEHAVIOR=develop` profile, not for a static nginx host.
