# cedar-cli


```bash

export CEDAR_HOME='/Users/cedar-dev/CEDAR/'

cd ${CEDAR_HOME}
git clone https://github.com/metadatacenter/cedar-cli

cd cedar-cli
git checkout develop

python -m venv ./.venv
pip install -r requirements.txt
source .venv/bin/activate

python main.py --help

python main.py git clone docker
python main.py git clone all

python main.py git list
python main.py git status
python main.py git branch

python main.py git pull
python main.py git checkout <branch>


```