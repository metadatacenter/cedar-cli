import os

from org.metadatacenter.model.Repo import Repo


class Util(object):
    cedar_home = os.environ['CEDAR_HOME']

    def __new__(cls):
        if not hasattr(cls, 'instance'):
            cls.instance = super(Util, cls).__new__(cls)
        return cls.instance

    @staticmethod
    def get_wd(repo: Repo):
        return Util.cedar_home + "/" + repo.get_wd()

    @staticmethod
    def get_flat_repo_list(repo_list):
        repos = []
        for repo in repo_list:
            repos.append(repo)
            if len(repo.sub_repos) > 0:
                for sub_repo in repo.sub_repos:
                    repos.append(sub_repo)
        return repos
