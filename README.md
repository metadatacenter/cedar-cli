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