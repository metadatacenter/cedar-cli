from org.metadatacenter.model.CheckRunning import CheckRunning
from org.metadatacenter.model.Servers import Servers


class ServersFactory:

    def __init__(self):
        super().__init__()

    @staticmethod
    def build_servers():
        servers = Servers()
        servers.add_microservice('artifact', 1)
        servers.add_microservice('bridge', 15)
        servers.add_microservice('group', 9)
        servers.add_microservice('impex', 8)
        servers.add_microservice('messaging', 12)
        servers.add_microservice('monitor', 14)
        servers.add_microservice('open', 13)
        servers.add_microservice('repo', 2)
        servers.add_microservice('resource', 7)
        servers.add_microservice('schema', 3)
        servers.add_microservice('submission', 10)
        servers.add_microservice('terminology', 4)
        servers.add_microservice('user', 5)
        servers.add_microservice('valuerecommender', 6)
        servers.add_microservice('worker', 11)

        servers.add_infra('MongoDB', 27017, check_running=CheckRunning.OPEN_PORT)
        servers.add_infra('OpenSearch-REST', 9200)
        servers.add_infra('OpenSearch-Transport', 9300, check_running=CheckRunning.OPEN_PORT)
        servers.add_infra('NGINX', 80)
        servers.add_infra('Keycloak', 8080)
        servers.add_infra('Neo4j', 7474)
        servers.add_infra('Redis-persistent', 6379, check_running=CheckRunning.OPEN_PORT)
        servers.add_infra('MySQL', 3306, check_running=CheckRunning.OPEN_PORT)

        servers.add_frontend('main', 4200)
        servers.add_frontend('openview', 4220)
        servers.add_frontend('content', 4240)
        servers.add_frontend('monitoring', 4300)
        servers.add_frontend('artifacts', 4320)
        servers.add_frontend('bridging', 4340)

        servers.add_frontend_non_essential('cee-dev', 4400)
        servers.add_frontend_non_essential('demo.cee', 4260)
        servers.add_frontend_non_essential('docs.cee', 4280)

        return servers
