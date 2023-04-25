from json import JSONEncoder

from org.metadatacenter.model.Repo import Repo


class CustomJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Repo):
            json_repr = {'full_name': obj.get_wd()}
            return json_repr
        return obj.__dict__
