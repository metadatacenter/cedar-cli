import os

from rich.console import Console

from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.worker.Worker import Worker

console = Console()


class ProdWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def configure_frontends():
        sed = GlobalContext.get_sed_replace_in_place()
        domain = os.environ[Const.CEDAR_HOST]
        Worker.execute_generic_shell_commands([
            'echo "Updating frontend components to use proper domain"' + "\n" +
            sed + " 's/metadatacenter.org\\//" + domain + "\\//g' ${CEDAR_HOME}/cedar-template-editor/app/keycloak.json" + "\n" +
            sed + " 's/window.cedarDomain = \".*\"/window.cedarDomain = \"" + domain + "\"/g' ${CEDAR_HOME}/cedar-openview/cedar-openview-dist/index.html" + "\n" +
            sed + " 's/component.metadatacenter.org\\//component." + domain + "\\//g' ${CEDAR_HOME}/cedar-openview/cedar-openview-dist/index.html" + "\n" +
            sed + " 's/window.cedarDomain = \".*\"/window.cedarDomain = \"" + domain + "\"/g' ${CEDAR_HOME}/cedar-bridging/cedar-bridging-dist/index.html" + "\n" +
            sed + " 's/component.metadatacenter.org\\//component." + domain + "\\//g' ${CEDAR_HOME}/cedar-bridging/cedar-bridging-dist/index.html" + "\n" +
            sed + " 's/window.cedarDomain = \".*\"/window.cedarDomain = \"" + domain + "\"/g' ${CEDAR_HOME}/cedar-monitoring/cedar-monitoring-dist/index.html" + "\n" +
            sed + " 's/component.metadatacenter.org\\//component." + domain + "\\//g' ${CEDAR_HOME}/cedar-monitoring/cedar-monitoring-dist/index.html" + "\n" +
            sed + " 's/window.cedarDomain = \".*\"/window.cedarDomain = \"" + domain + "\"/g' ${CEDAR_HOME}/cedar-artifacts/cedar-artifacts-dist/index.html" + "\n" +
            sed + " 's/component.metadatacenter.org\\//component." + domain + "\\//g' ${CEDAR_HOME}/cedar-artifacts/cedar-artifacts-dist/index.html" + "\n"
        ],
            title="Updating frontend components",
        )

    @staticmethod
    def reset_frontends():
        sed = GlobalContext.get_sed_replace_in_place()
        domain = os.environ[Const.CEDAR_HOST]
        Worker.execute_generic_shell_commands([
            'echo "Reseting frontend components to use original domain"' + "\n" +
            "cd  ${CEDAR_HOME}/cedar-template-editor/" + "\n" +
            "git checkout ${CEDAR_HOME}/cedar-template-editor/app/keycloak.json" + "\n" +
            "cd  ${CEDAR_HOME}/cedar-openview/" + "\n" +
            "git checkout ${CEDAR_HOME}/cedar-openview/cedar-openview-dist/index.html" + "\n" +
            "cd  ${CEDAR_HOME}/cedar-bridging/" + "\n" +
            "git checkout ${CEDAR_HOME}/cedar-bridging/cedar-bridging-dist/index.html" + "\n" +
            "cd  ${CEDAR_HOME}/cedar-monitoring/" + "\n" +
            "git checkout ${CEDAR_HOME}/cedar-monitoring/cedar-monitoring-dist/index.html" + "\n" +
            "cd  ${CEDAR_HOME}/cedar-artifacts/" + "\n" +
            "git checkout ${CEDAR_HOME}/cedar-artifacts/cedar-artifacts-dist/index.html" + "\n"
        ],
            title="Reseting frontend components",
        )
