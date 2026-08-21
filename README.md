# cedar-cli
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

## Cheat sheet
The full set of commands and subcommands will be shown as a `pdf` file after executing:
```bash
cedarcli cheat
```

![CEDAR CLI commands](assets/docs/cedar-cli.png?raw=true "CEDAR CLI commands")

## Split frontend publication and native deployment

Workspace and Template Designer remain outside `release all-in-one` until migration acceptance.
They are also excluded from the generic `deploy frontends` and `deploy all` commands, so an ordinary
legacy deployment cannot publish or activate them accidentally.

Use the explicit commands when preparing a split-frontend build:

```bash
# Reproducibly install the exact locked dependencies in both Git checkouts.
cedarcli build split-frontends

# On a native staging/production host, configure both static trees and write build identity.
# This requires CEDAR_FRONTEND_BEHAVIOR=server and the exact environment frontend URLs.
cedarcli build split-frontends --server-payload

# Publish both current package versions to the Nexus registries declared by their package.json files.
cedarcli deploy split-frontends --dry-run
cedarcli deploy split-frontends

# Run or stop both development Gulp servers locally on ports 4201 and 4202.
cedarcli start frontend split-frontends
cedarcli stop frontend split-frontends
```

`deploy` retains cedarcli's historical meaning of publishing build artifacts: it runs `npm ci` and
`npm publish` in each repository. It does not change DNS, certificates, nginx, Keycloak, CORS, or
public routing. Those environment operations remain separate acceptance and cutover gates.

Staging and production need no Docker. Their nginx virtual hosts serve
`cedar-workspace/app` and `cedar-template-designer/app` directly after `--server-payload` exits, just
as the existing native deployment serves the monolith's `app` tree. The start/stop commands are for
the local `CEDAR_FRONTEND_BEHAVIOR=develop` profile, not for a static nginx host.
