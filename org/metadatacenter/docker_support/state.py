"""CEDAR docker state."""
from __future__ import annotations
from org.metadatacenter.model.DockerDeploymentMode import DockerDeploymentMode
from org.metadatacenter.util.Util import Util
import json
import os


def _deployment_state_path():
    return os.path.join(Util.cedar_home, '.cedar', 'docker-deployment.json')


def active_deployment():
    """Return the last aggregate deployment mode, if recorded."""
    try:
        with open(_deployment_state_path(), 'r', encoding='utf-8') as state_file:
            state = json.load(state_file)
        return DockerDeploymentMode(state['mode'])
    except (FileNotFoundError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _record_active_deployment(mode, train=None):
    state_path = _deployment_state_path()
    state_directory = os.path.dirname(state_path)
    os.makedirs(state_directory, exist_ok=True)
    temporary_path = state_path + '.tmp'
    with open(temporary_path, 'w', encoding='utf-8') as state_file:
        state = {'mode': mode.value}
        if train:
            state['train'] = train
        json.dump(state, state_file, indent=2)
        state_file.write('\n')
    os.replace(temporary_path, state_path)


def _clear_active_deployment():
    try:
        os.remove(_deployment_state_path())
    except FileNotFoundError:
        pass


def active_train():
    try:
        with open(_deployment_state_path(), 'r', encoding='utf-8') as state_file:
            return json.load(state_file).get('train')
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return None
