"""CEDAR docker policy."""
from __future__ import annotations


GIT_STATUS_CHAR_LIMIT = 300


FRONTEND_NAMES = (
    'EDITOR',
    'CONTENT',
    'OPENVIEW',
    'MONITORING',
    'BRIDGING',
    'WORKSPACE',
    'DESIGNER',
)


FRONTEND_PUBLIC_HOSTS = (
    'cedar',
    'workspace',
    'designer',
    'openview',
    'content',
    'monitoring',
    'bridging',
)


FRONTEND_COMPOSE_SERVICES = {
    'main': 'frontend-main',
    'openview': 'frontend-openview',
    'monitoring': 'frontend-monitoring',
    'bridging': 'frontend-bridging',
    'content': 'frontend-content',
    'workspace': 'frontend-workspace',
    'designer': 'frontend-template-designer',
}


MICROSERVICE_COMPOSE_SERVICES = {
    'artifact': 'server-artifact',
    'bridge': 'server-bridge',
    'group': 'server-group',
    'impex': 'server-impex',
    'messaging': 'server-messaging',
    'monitor': 'server-monitor',
    'open': 'server-openview',
    'repo': 'server-repo',
    'resource': 'server-resource',
    'schema': 'server-schema',
    'submission': 'server-submission',
    'terminology': 'server-terminology',
    'user': 'server-user',
    'valuerecommender': 'server-valuerecommender',
    'worker': 'server-worker',
}


STATUS_SERVICE_ORDER = {
    'infrastructure': (
        'mysql',
        'mongo',
        'redis-persistent',
        'opensearch',
        'neo4j',
        'keycloak',
        'nginx',
    ),
    'microservices': tuple(MICROSERVICE_COMPOSE_SERVICES.values()),
    'frontends': tuple(FRONTEND_COMPOSE_SERVICES.values()),
    'admin': (
        'redis-commander',
        'kibana',
        'phpmyadmin',
        'admin-tool',
    ),
}


MICROSERVICE_WRITABLE_VOLUMES = (
    'log_artifact',
    'log_bridge',
    'log_group',
    'log_impex',
    'log_messaging',
    'log_monitor',
    'log_openview',
    'log_repo',
    'log_resource',
    'log_schema',
    'log_submission',
    'log_terminology',
    'log_user',
    'log_valuerecommender',
    'log_worker',
    'resource_state',
    'terminology_data',
)


# The frontend containers run nginx as the image's own unprivileged user (uid 101), and their
# nginx configs write into these volumes, so ones created by older root-running images need the
# same one-time ownership treatment the microservice volumes get.
FRONTEND_LOG_VOLUMES = (
    'log_frontend_main',
    'log_frontend_content',
    'log_frontend_openview',
    'log_frontend_monitoring',
    'log_frontend_bridging',
    'log_frontend_workspace',
    'log_frontend_template_designer',
)


STACKS = {
    'infrastructure': ('cedar-infrastructure', 'infrastructure services'),
    'microservices': ('cedar-microservices', 'microservices'),
    'frontends': ('cedar-frontend', 'frontends'),
    'admin': ('cedar-admin', 'admin tools'),
}
