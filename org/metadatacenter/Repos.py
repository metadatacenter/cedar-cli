from org.metadatacenter.Repo import Repo


class Repos:
    def __init__(self):
        self.map = {}
        self.map["cedar-parent"] = Repo("cedar-parent", "java-wrapper")
        self.map["cedar-libraries"] = Repo("cedar-libraries", "java-wrapper")
        self.map["cedar-project"] = Repo("cedar-project", "java-wrapper")
        self.map["cedar-clients"] = Repo("cedar-clients", "java-wrapper")

        self.map["cedar-artifact-library"] = Repo("cedar-artifact-library", "java", is_library=True)
        self.map["cedar-config-library"] = Repo("cedar-config-library", "java", is_library=True)
        self.map["cedar-core-library"] = Repo("cedar-core-library", "java", is_library=True)
        self.map["cedar-model-library"] = Repo("cedar-model-library", "java", is_library=True)
        self.map["cedar-model-validation-library"] = Repo("cedar-model-validation-library", "java", is_library=True)
        self.map["cedar-rest-library"] = Repo("cedar-rest-library", "java", is_library=True)

        self.map["cedar-archetype-exporter"] = Repo("cedar-archetype-exporter", "java", is_client=True)
        self.map["cedar-archetype-instance-reader"] = Repo("cedar-archetype-instance-reader", "java", is_client=True)
        self.map["cedar-archetype-instance-writer"] = Repo("cedar-archetype-instance-writer", "java", is_client=True)

        self.map["cedar-artifact-server"] = Repo("cedar-artifact-server", "java", is_microservice=True)
        self.map["cedar-messaging-server"] = Repo("cedar-messaging-server", "java", is_microservice=True)
        self.map["cedar-repo-server"] = Repo("cedar-repo-server", "java", is_microservice=True)
        self.map["cedar-resource-server"] = Repo("cedar-resource-server", "java", is_microservice=True)
        self.map["cedar-schema-server"] = Repo("cedar-schema-server", "java", is_microservice=True)
        self.map["cedar-submission-server"] = Repo("cedar-submission-server", "java", is_microservice=True)
        self.map["cedar-terminology-server"] = Repo("cedar-terminology-server", "java", is_microservice=True)
        self.map["cedar-user-server"] = Repo("cedar-user-server", "java", is_microservice=True)
        self.map["cedar-valuerecommender-server"] = Repo("cedar-valuerecommender-server", "java", is_microservice=True)
        self.map["cedar-worker-server"] = Repo("cedar-worker-server", "java", is_microservice=True)
        self.map["cedar-monitor-server"] = Repo("cedar-monitor-server", "java", is_microservice=True)
        self.map["cedar-openview-server"] = Repo("cedar-openview-server", "java", is_microservice=True)
        self.map["cedar-group-server"] = Repo("cedar-group-server", "java", is_microservice=True)
        self.map["cedar-impex-server"] = Repo("cedar-impex-server", "java", is_microservice=True)

        self.map["cedar-keycloak-event-listener"] = Repo("cedar-keycloak-event-listener", "java")
        self.map["cedar-microservice-libraries"] = Repo("cedar-microservice-libraries", "java")
        self.map["cedar-admin-tool"] = Repo("cedar-admin-tool", "java")
        self.map["cedar-cadsr-tools"] = Repo("cedar-cadsr-tools", "java")

        self.map["cedar-template-editor"] = Repo("cedar-template-editor", "angularJS")

        self.map["cedar-artifacts"] = Repo("cedar-artifacts", "angular")
        self.map["cedar-artifacts-dist"] = Repo("cedar-artifacts-dist", "js", dist_src="cedar-artifacts")

        self.map["cedar-monitoring"] = Repo("cedar-monitoring", "angular")
        self.map["cedar-monitoring-dist"] = Repo("cedar-monitoring-dist", "js", dist_src="cedar-monitoring")

        self.map["cedar-openview"] = Repo("cedar-openview", "angular")
        self.map["cedar-openview-dist"] = Repo("cedar-openview-dist", "js", dist_src="cedar-openview")

        self.map["cedar-artifacts"] = Repo("cedar-artifacts", "angular")
        self.map["cedar-artifacts-dist"] = Repo("cedar-artifacts-dist", "js", dist_src="cedar-artifacts")

        self.map["cedar-embeddable-editor"] = Repo("cedar-embeddable-editor", "angular")
        self.map["cedar-metadata-form"] = Repo("cedar-metadata-form", "angular")

        self.map["cedar-cee-demo-angular"] = Repo("cedar-cee-demo-angular", "angular")
        self.map["cedar-cee-demo-angular-dist"] = Repo("cedar-cee-demo-angular-dist", "js", dist_src="cedar-cee-demo-angular")

        self.map["cedar-cee-docs-angular"] = Repo("cedar-cee-docs-angular", "angular")
        self.map["cedar-cee-docs-angular-dist"] = Repo("cedar-cee-docs-angular-dist", "js", dist_src="cedar-cee-docs-angular")

        self.map["cedar-mkdocs"] = Repo("cedar-mkdocs", "mkdocs")
        self.map["cedar-mkdocs-developer"] = Repo("cedar-mkdocs-developer", "mkdocs", is_private=True)

        self.map["cedar-component-distribution"] = Repo("cedar-component-distribution", "content-delivery")
        self.map["cedar-shared-data"] = Repo("cedar-shared-data", "content-delivery")
        self.map["cedar-swagger-ui"] = Repo("cedar-swagger-ui", "content-delivery")

        self.map["cedar-docker-build"] = Repo("cedar-docker-build", "docker", for_docker=True)
        self.map["cedar-docker-deploy"] = Repo("cedar-docker-deploy", "docker", for_docker=True)

        self.map["cedar-development"] = Repo("cedar-development", "misc", for_docker=True)
        self.map["cedar-docs"] = Repo("cedar-docs", "misc")
        self.map["cedar-util"] = Repo("cedar-util", "misc")

        self.map["cedar-cee-demo-api-php"] = Repo("cedar-cee-demo-api-php", "php")

    def get_list(self):
        return list(self.map.values())

    def get_for_docker_list(self):
        return [repo for repo in list(self.map.values()) if repo.for_docker is True]
