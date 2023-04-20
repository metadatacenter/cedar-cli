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
