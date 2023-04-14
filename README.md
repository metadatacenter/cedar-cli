# cedar-cli


```bash

gocedar
cd cedar-cli

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