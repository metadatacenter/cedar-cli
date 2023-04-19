from rich.console import Console

from org.metadatacenter.model.Repo import Repo

console = Console()


class Repos:
    def __init__(self):
        self.map = {}
        self.add_repo(Repo("cedar-parent", "java-wrapper", expected_build_lines=20))
        self.add_repo(Repo("cedar-libraries", "java-wrapper", expected_build_lines=705))
        self.add_repo(Repo("cedar-project", "java-wrapper", expected_build_lines=26502))
        self.add_repo(Repo("cedar-clients", "java-wrapper", expected_build_lines=167))

        self.add_repo(Repo("cedar-artifact-library", "java", is_library=True))
        self.add_repo(Repo("cedar-config-library", "java", is_library=True))
        self.add_repo(Repo("cedar-core-library", "java", is_library=True))
        self.add_repo(Repo("cedar-model-library", "java", is_library=True))
        self.add_repo(Repo("cedar-model-validation-library", "java", is_library=True))
        self.add_repo(Repo("cedar-rest-library", "java", is_library=True))

        self.add_repo(Repo("cedar-archetype-exporter", "java", is_client=True))
        self.add_repo(Repo("cedar-archetype-instance-reader", "java", is_client=True))
        self.add_repo(Repo("cedar-archetype-instance-writer", "java", is_client=True))

        self.add_repo(Repo("cedar-artifact-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-messaging-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-repo-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-resource-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-schema-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-submission-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-terminology-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-user-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-valuerecommender-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-worker-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-monitor-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-openview-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-group-server", "java", is_microservice=True))
        self.add_repo(Repo("cedar-impex-server", "java", is_microservice=True))

        self.add_repo(Repo("cedar-keycloak-event-listener", "java"))
        self.add_repo(Repo("cedar-microservice-libraries", "java"))
        self.add_repo(Repo("cedar-admin-tool", "java"))
        self.add_repo(Repo("cedar-cadsr-tools", "java"))

        self.add_repo(Repo("cedar-template-editor", "angularJS", is_frontend=True, expected_build_lines=14))

        self.add_repo(Repo("cedar-artifacts", "angular", is_frontend=True, expected_build_lines=24))
        self.add_repo(Repo("cedar-artifacts-dist", "js", is_frontend=True))

        self.add_repo(Repo("cedar-monitoring", "angular", is_frontend=True, expected_build_lines=24))
        self.add_repo(Repo("cedar-monitoring-dist", "js", is_frontend=True))

        openview_src = Repo("cedar-openview", "angular", is_frontend=True, expected_build_lines=30)
        openview_dist = Repo("cedar-openview-dist", "angular-dist", is_frontend=True)

        openview_multi = Repo("cedar-openview", "multi", is_frontend=True)
        openview_multi.add_sub_repo(openview_src)
        openview_multi.add_sub_repo(openview_dist)

        self.add_repo(openview_multi)

        self.add_repo(Repo("cedar-embeddable-editor", "angular", is_frontend=True, expected_build_lines=29))
        self.add_repo(Repo("cedar-metadata-form", "angular", is_frontend=True, expected_build_lines=31))

        self.add_repo(Repo("cedar-cee-demo-angular", "angular", is_frontend=True, expected_build_lines=26))
        self.add_repo(Repo("cedar-cee-demo-angular-dist", "js", is_frontend=True))

        self.add_repo(Repo("cedar-cee-docs-angular", "angular", is_frontend=True, expected_build_lines=40))
        self.add_repo(Repo("cedar-cee-docs-angular-dist", "js", is_frontend=True))

        self.add_repo(Repo("cedar-mkdocs", "mkdocs"))
        self.add_repo(Repo("cedar-mkdocs-developer", "mkdocs", is_private=True))

        self.add_repo(Repo("cedar-component-distribution", "content-delivery"))
        self.add_repo(Repo("cedar-shared-data", "content-delivery"))
        self.add_repo(Repo("cedar-swagger-ui", "content-delivery"))

        self.add_repo(Repo("cedar-docker-build", "docker", for_docker=True))
        self.add_repo(Repo("cedar-docker-deploy", "docker", for_docker=True))

        self.add_repo(Repo("cedar-development", "misc", for_docker=True))
        self.add_repo(Repo("cedar-util", "misc"))

        self.add_repo(Repo("cedar-cli", "python"))

        self.add_repo(Repo("cedar-cee-demo-api-php", "php"))

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
            if repo.repo_type == "java-wrapper" and "parent" in repo.name:
                return repo

    def get_libraries(self):
        for name, repo in self.map.items():
            if repo.repo_type == "java-wrapper" and "libraries" in repo.name:
                return repo

    def get_project(self):
        for name, repo in self.map.items():
            if repo.repo_type == "java-wrapper" and "project" in repo.name:
                return repo

    def get_clients(self):
        for name, repo in self.map.items():
            if repo.repo_type == "java-wrapper" and "clients" in repo.name:
                return repo

    def get_frontends(self):
        repos = []
        for name, repo in self.map.items():
            if repo.is_frontend:
                repos.append(repo)
        return repos
