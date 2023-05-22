# cedar-cli


```bash

export CEDAR_HOME='/Users/cedar-dev/CEDAR/'

cd ${CEDAR_HOME}
git clone https://github.com/metadatacenter/cedar-cli

cd cedar-cli
git checkout develop

python -m venv ./.venv
source .venv/bin/activate
pip install -r requirements.txt

python cedar.py --help

python cedar.py git clone docker
python cedar.py git clone all

python cedar.py git list
python cedar.py git status
python cedar.py git branch

python cedar.py git pull
python cedar.py git checkout <branch>

python cedar.py git pull
python cedar.py git checkout <branch>


python cedar.py build parent
python cedar.py build libraries
python cedar.py build clients
python cedar.py build project
python cedar.py build all
python cedar.py build frontends


```

or use the alias


```bash
cedarcli build all
```