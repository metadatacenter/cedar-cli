from rich.console import Console

from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoType import RepoType

console = Console()


class Repos:
    def __init__(self):
        self.map = {}
        self.add_repo(Repo("cedar-parent", RepoType.JAVA_WRAPPER, expected_build_lines=20))
        self.add_repo(Repo("cedar-libraries", RepoType.JAVA_WRAPPER, expected_build_lines=705))
        self.add_repo(Repo("cedar-project", RepoType.JAVA_WRAPPER, expected_build_lines=26502))
        self.add_repo(Repo("cedar-clients", RepoType.JAVA_WRAPPER, expected_build_lines=167))

        self.add_repo(Repo("cedar-artifact-library", RepoType.JAVA, is_library=True))
        self.add_repo(Repo("cedar-config-library", RepoType.JAVA, is_library=True))
        self.add_repo(Repo("cedar-core-library", RepoType.JAVA, is_library=True))
        self.add_repo(Repo("cedar-model-library", RepoType.JAVA, is_library=True))
        self.add_repo(Repo("cedar-model-validation-library", RepoType.JAVA, is_library=True))
        self.add_repo(Repo("cedar-rest-library", RepoType.JAVA, is_library=True))

        self.add_repo(Repo("cedar-archetype-exporter", RepoType.JAVA, is_client=True))
        self.add_repo(Repo("cedar-archetype-instance-reader", RepoType.JAVA, is_client=True))
        self.add_repo(Repo("cedar-archetype-instance-writer", RepoType.JAVA, is_client=True))

        self.add_repo(Repo("cedar-artifact-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-messaging-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-repo-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-resource-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-schema-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-submission-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-terminology-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-user-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-valuerecommender-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-worker-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-monitor-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-openview-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-group-server", RepoType.JAVA, is_microservice=True))
        self.add_repo(Repo("cedar-impex-server", RepoType.JAVA, is_microservice=True))

        self.add_repo(Repo("cedar-keycloak-event-listener", RepoType.JAVA))
        self.add_repo(Repo("cedar-microservice-libraries", RepoType.JAVA))
        self.add_repo(Repo("cedar-admin-tool", RepoType.JAVA))
        self.add_repo(Repo("cedar-cadsr-tools", RepoType.JAVA))

        self.add_repo(Repo("cedar-template-editor", RepoType.ANGULAR_JS, is_frontend=True, expected_build_lines=14))

        artifacts_src = Repo("cedar-artifacts", RepoType.ANGULAR, is_frontend=True, expected_build_lines=30)
        artifacts_dist = Repo("cedar-artifacts-dist", RepoType.ANGULAR_DIST, is_frontend=True)
        artifacts_multi = Repo("cedar-artifacts", RepoType.MULTI, is_frontend=True)
        artifacts_multi.add_sub_repo(artifacts_src)
        artifacts_multi.add_sub_repo(artifacts_dist)
        self.add_repo(artifacts_multi)

        monitoring_src = Repo("cedar-monitoring", RepoType.ANGULAR, is_frontend=True, expected_build_lines=30)
        monitoring_dist = Repo("cedar-monitoring-dist", RepoType.ANGULAR_DIST, is_frontend=True)
        monitoring_multi = Repo("cedar-monitoring", RepoType.MULTI, is_frontend=True)
        monitoring_multi.add_sub_repo(monitoring_src)
        monitoring_multi.add_sub_repo(monitoring_dist)
        self.add_repo(monitoring_multi)

        openview_src = Repo("cedar-openview", RepoType.ANGULAR, is_frontend=True, expected_build_lines=30)
        openview_dist = Repo("cedar-openview-dist", RepoType.ANGULAR_DIST, is_frontend=True)
        openview_multi = Repo("cedar-openview", RepoType.MULTI, is_frontend=True)
        openview_multi.add_sub_repo(openview_src)
        openview_multi.add_sub_repo(openview_dist)
        self.add_repo(openview_multi)

        self.add_repo(Repo("cedar-embeddable-editor", RepoType.ANGULAR, is_frontend=True, expected_build_lines=29))
        self.add_repo(Repo("cedar-metadata-form", "angular", is_frontend=True, expected_build_lines=31))

        self.add_repo(Repo("cedar-cee-demo-angular", RepoType.ANGULAR, is_frontend=True, expected_build_lines=26))
        self.add_repo(Repo("cedar-cee-demo-angular-dist", "js", is_frontend=True))

        self.add_repo(Repo("cedar-cee-docs-angular", RepoType.ANGULAR, is_frontend=True, expected_build_lines=40))
        self.add_repo(Repo("cedar-cee-docs-angular-dist", "js", is_frontend=True))

        self.add_repo(Repo("cedar-mkdocs", RepoType.MKDOCS))
        self.add_repo(Repo("cedar-mkdocs-developer", RepoType.MKDOCS, is_private=True))

        self.add_repo(Repo("cedar-component-distribution", RepoType.CONTENT_DELIVERY))
        self.add_repo(Repo("cedar-shared-data", RepoType.CONTENT_DELIVERY))
        self.add_repo(Repo("cedar-swagger-ui", RepoType.CONTENT_DELIVERY))

        self.add_repo(Repo("cedar-docker-build", RepoType.DOCKER, for_docker=True))
        self.add_repo(Repo("cedar-docker-deploy", RepoType.DOCKER, for_docker=True))

        self.add_repo(Repo("cedar-development", RepoType.MISC, for_docker=True))
        self.add_repo(Repo("cedar-util", RepoType.MISC))

        self.add_repo(Repo("cedar-cli", RepoType.PYTHON))

        self.add_repo(Repo("cedar-cee-demo-api-php", RepoType.PHP))

    def add_repo(self, repo):
        name = repo.name
        if name in self.map:
            console.log("Repo already present in registry:" + name)
        else:
            self.map[name] = repo

    def get_list_top(self):
        return list(self.map.values())

    def get_list_all(self):
        repos = []
        for name, repo in self.map.items():
            repos.append(repo)
            if len(repo.sub_repos) > 0:
                for sub_repo in repo.sub_repos:
                    repos.append(sub_repo)
        return repos

    def get_for_docker_list(self):
        return [repo for repo in list(self.map.values()) if repo.for_docker is True]

    def get_parent(self):
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "parent" in repo.name:
                return repo

    def get_libraries(self):
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "libraries" in repo.name:
                return repo

    def get_project(self):
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "project" in repo.name:
                return repo

    def get_clients(self):
        for name, repo in self.map.items():
            if repo.repo_type == RepoType.JAVA_WRAPPER and "clients" in repo.name:
                return repo

    def get_frontends(self):
        repos = []
        for name, repo in self.map.items():
            if repo.is_frontend:
                repos.append(repo)
        return repos
