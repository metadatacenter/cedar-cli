from org.metadatacenter.model.Subdomains import Subdomains


class SubdomainsFactory:

    def __init__(self):
        super().__init__()

    @staticmethod
    def build_subdomains():
        subdomains = Subdomains()

        subdomains.add_redirect('')

        subdomains.add_backend('auth', 8443)

        subdomains.add_microservice('artifact')
        subdomains.add_microservice('bridge')
        subdomains.add_microservice('group')
        subdomains.add_microservice('impex')
        subdomains.add_microservice('messaging')
        subdomains.add_microservice('monitor')
        subdomains.add_microservice('open')
        subdomains.add_microservice('repo')
        subdomains.add_microservice('resource')
        subdomains.add_microservice('schema')
        subdomains.add_microservice('submission')
        subdomains.add_microservice('terminology')
        subdomains.add_microservice('user')
        subdomains.add_microservice('valuerecommender')
        subdomains.add_microservice('worker')

        subdomains.add_frontend('cedar')
        subdomains.add_frontend('openview')
        subdomains.add_frontend('content')
        subdomains.add_frontend('monitoring')
        subdomains.add_frontend('artifacts')
        subdomains.add_frontend('bridging')

        subdomains.add_frontend('demo.cee')
        subdomains.add_frontend('docs.cee')
        subdomains.add_frontend('demo-dist.cee')
        subdomains.add_frontend('docs-dist.cee')

        subdomains.add_static('shared', static_location='cedar-shared-data')

        return subdomains
