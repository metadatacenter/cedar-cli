from org.metadatacenter.model.Repo import Repo
from org.metadatacenter.model.RepoRelation import RepoRelation
from org.metadatacenter.model.RepoRelationType import RepoRelationType
from org.metadatacenter.model.RepoType import RepoType
from org.metadatacenter.model.Repos import Repos


class ReposFactory:
    git_base = "https://github.com/metadatacenter/"

    def __init__(self):
        super().__init__()

    @staticmethod
    def build_repos():
        repos = Repos()
        repos.add_repo(Repo("cedar-parent", RepoType.JAVA_WRAPPER))
        repos.add_repo(Repo("cedar-libraries", RepoType.JAVA_WRAPPER))
        repos.add_repo(Repo("cedar-project", RepoType.JAVA_WRAPPER))
        repos.add_repo(Repo("cedar-clients", RepoType.JAVA_WRAPPER))

        repos.add_repo(Repo("cedar-artifact-library", RepoType.JAVA, is_library=True))
        repos.add_repo(Repo("cedar-config-library", RepoType.JAVA, is_library=True))
        repos.add_repo(Repo("cedar-core-library", RepoType.JAVA, is_library=True))
        repos.add_repo(Repo("cedar-model-library", RepoType.JAVA, is_library=True))
        repos.add_repo(Repo("cedar-model-validation-library", RepoType.JAVA, is_library=True))
        repos.add_repo(Repo("cedar-rest-library", RepoType.JAVA, is_library=True))

        repos.add_repo(Repo("cedar-archetype-exporter", RepoType.JAVA, is_client=True))
        repos.add_repo(Repo("cedar-archetype-instance-reader", RepoType.JAVA, is_client=True))
        repos.add_repo(Repo("cedar-archetype-instance-writer", RepoType.JAVA, is_client=True))

        repos.add_repo(Repo("cedar-artifact-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-messaging-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-repo-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-resource-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-schema-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-submission-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-terminology-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-user-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-valuerecommender-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-worker-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-monitor-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-openview-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-group-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-impex-server", RepoType.JAVA, is_microservice=True))
        repos.add_repo(Repo("cedar-bridge-server", RepoType.JAVA, is_microservice=True))

        repos.add_repo(Repo("cedar-keycloak-event-listener", RepoType.JAVA))
        repos.add_repo(Repo("cedar-microservice-libraries", RepoType.JAVA))
        repos.add_repo(Repo("cedar-admin-tool", RepoType.JAVA))
        repos.add_repo(Repo("cedar-cadsr-tools", RepoType.JAVA))

        repos.add_repo(Repo("cedar-template-editor", RepoType.ANGULAR_JS, is_frontend=True))

        artifacts_multi = Repo("cedar-artifacts", RepoType.MULTI, is_frontend=True)
        artifacts_src = Repo("cedar-artifacts-src", RepoType.ANGULAR, is_frontend=True)
        artifacts_dist = Repo("cedar-artifacts-dist", RepoType.ANGULAR_DIST, is_frontend=True)

        artifacts_multi.add_sub_repo(artifacts_src)
        artifacts_multi.add_sub_repo(artifacts_dist)
        artifacts_src_dist_relation = RepoRelation(artifacts_src, RepoRelationType.IS_SOURCE_OF, artifacts_dist)
        repos.add_relation(artifacts_src_dist_relation)
        repos.add_repo(artifacts_multi)

        monitoring_multi = Repo("cedar-monitoring", RepoType.MULTI, is_frontend=True)
        monitoring_src = Repo("cedar-monitoring-src", RepoType.ANGULAR, is_frontend=True)
        monitoring_dist = Repo("cedar-monitoring-dist", RepoType.ANGULAR_DIST, is_frontend=True)

        monitoring_multi.add_sub_repo(monitoring_src)
        monitoring_multi.add_sub_repo(monitoring_dist)
        monitoring_src_dist_relation = RepoRelation(monitoring_src, RepoRelationType.IS_SOURCE_OF, monitoring_dist)
        repos.add_relation(monitoring_src_dist_relation)
        repos.add_repo(monitoring_multi)

        openview_multi = Repo("cedar-openview", RepoType.MULTI, is_frontend=True)
        openview_src = Repo("cedar-openview-src", RepoType.ANGULAR, is_frontend=True)
        openview_dist = Repo("cedar-openview-dist", RepoType.ANGULAR_DIST, is_frontend=True)

        openview_multi.add_sub_repo(openview_src)
        openview_multi.add_sub_repo(openview_dist)
        openview_src_dist_relation = RepoRelation(openview_src, RepoRelationType.IS_SOURCE_OF, openview_dist)
        repos.add_relation(openview_src_dist_relation)
        repos.add_repo(openview_multi)

        cee_demo_angular_multi = Repo("cedar-cee-demo", RepoType.MULTI, is_frontend=True)
        cee_demo_angular_src = Repo("cedar-cee-demo-angular-src", RepoType.ANGULAR, is_frontend=True)
        cee_demo_angular_dist = Repo("cedar-cee-demo-angular-dist", RepoType.ANGULAR_DIST, is_frontend=True)
        cee_docs_angular_src = Repo("cedar-cee-docs-angular-src", RepoType.ANGULAR, is_frontend=True)
        cee_docs_angular_dist = Repo("cedar-cee-docs-angular-dist", RepoType.ANGULAR_DIST, is_frontend=True)
        cee_demo_api_php = Repo("cedar-cee-demo-api-php", RepoType.PHP, is_frontend=True)

        cee_demo_angular_multi.add_sub_repo(cee_demo_angular_src)
        cee_demo_angular_multi.add_sub_repo(cee_demo_angular_dist)
        cee_demo_angular_src_dist_relation = RepoRelation(cee_demo_angular_src, RepoRelationType.IS_SOURCE_OF, cee_demo_angular_dist)
        repos.add_relation(cee_demo_angular_src_dist_relation)

        cee_demo_angular_multi.add_sub_repo(cee_docs_angular_src)
        cee_demo_angular_multi.add_sub_repo(cee_docs_angular_dist)
        cee_docs_angular_src_dist_relation = RepoRelation(cee_docs_angular_src, RepoRelationType.IS_SOURCE_OF, cee_docs_angular_dist)
        repos.add_relation(cee_docs_angular_src_dist_relation)

        cee_demo_angular_multi.add_sub_repo(cee_demo_api_php)
        repos.add_repo(cee_demo_angular_multi)

        embeddable_editor = Repo("cedar-embeddable-editor", RepoType.ANGULAR, is_frontend=True)
        repos.add_repo(embeddable_editor)

        metadata_form = Repo("cedar-metadata-form", "angular", is_frontend=True)
        repos.add_repo(metadata_form)

        component_distribution = Repo("cedar-component-distribution", RepoType.ANGULAR_DIST)
        repos.add_repo(component_distribution)
        embeddable_editor_dist_relation = RepoRelation(embeddable_editor, RepoRelationType.IS_SOURCE_OF, component_distribution,
                                                       parameters={
                                                           RepoRelation.TARGET_SUB_FOLDER: "cedar-embeddable-editor",
                                                           RepoRelation.SOURCE_SELECTOR: "{runtime,polyfills,main}.js",
                                                           RepoRelation.DESTINATION_CONCAT: 'cedar-embeddable-editor-${CEDAR_VERSION}.js'
                                                       })
        metadata_form_dist_relation = RepoRelation(metadata_form, RepoRelationType.IS_SOURCE_OF, component_distribution,
                                                   parameters={
                                                       RepoRelation.SOURCE_SUB_FOLDER: "dist/cedar-form",
                                                       RepoRelation.TARGET_SUB_FOLDER: "cedar-form",
                                                       RepoRelation.SOURCE_SELECTOR: '{runtime,polyfills,main}.js',
                                                       RepoRelation.DESTINATION_CONCAT: 'cedar-form-${CEDAR_VERSION}.js'
                                                   })
        repos.add_relation(embeddable_editor_dist_relation)
        repos.add_relation(metadata_form_dist_relation)

        repos.add_repo(Repo("cedar-mkdocs", RepoType.MKDOCS))
        repos.add_repo(Repo("cedar-mkdocs-developer", RepoType.MKDOCS, is_private=True))

        repos.add_repo(Repo("cedar-shared-data", RepoType.CONTENT_DELIVERY))
        repos.add_repo(Repo("cedar-swagger-ui", RepoType.CONTENT_DELIVERY))

        repos.add_repo(Repo("cedar-docker-build", RepoType.DOCKER_BUILD, for_docker=True))
        repos.add_repo(Repo("cedar-docker-deploy", RepoType.DOCKER_DEPLOY, for_docker=True))

        repos.add_repo(Repo("cedar-development", RepoType.DEVELOPMENT, for_docker=True))
        repos.add_repo(Repo("cedar-util", RepoType.MISC))

        repos.add_repo(Repo("cedar-cli", RepoType.PYTHON))

        return repos