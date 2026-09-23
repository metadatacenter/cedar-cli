import json
import shutil
from typing import Dict, List, Optional

from rich.console import Console
from rich.table import Table

from org.metadatacenter.util.GlobalContext import GlobalContext
from org.metadatacenter.util.ModeManager import ModeManager
from org.metadatacenter.util.ProcessRunner import run_shell
from org.metadatacenter.worker.Worker import Worker

console = Console()

# As artifactServer.collections in cedar-main.yml names them
ARTIFACT_COLLECTIONS = ["templates", "template-elements", "template-fields", "template-instances"]

# Written for both the modern shell and the legacy one, so a host carrying either can answer:
# ES5 only, and one tab-separated line per collection.
PROBE_SCRIPT = """
var names = %s;
for (var i = 0; i < names.length; i++) {
  var name = names[i];
  var unique = false;
  var indexes = db[name].getIndexes();
  for (var j = 0; j < indexes.length; j++) {
    if (indexes[j].key && indexes[j].key["@id"] && indexes[j].unique) { unique = true; }
  }
  print(name + "\\t" + db[name].count() + "\\t" + (unique ? "unique" : "missing"));
}
"""


class StoreWorker(Worker):
    """
    What the artifact document store carries, rather than what a provisioning step was supposed to
    leave behind.

    Each artifact collection needs a unique index on `@id`: no two documents may share an
    identifier, and every artifact read, replace and delete addresses its document by that field.
    Nothing in a server creates that index, and the two things that do apply once and never
    again - the Mongo image's create-indices.js on a container's first boot, and the admin tool's
    artifactServer-initDB when a person runs it. A store that went through neither enforces no
    uniqueness and answers every lookup with a collection scan, which a production store did for
    years with nothing reporting it.

    This check reads index metadata through the Mongo shell the host already has. It creates
    nothing: a unique index cannot be built over a collection that already holds a repeated
    identifier, so building one is a provisioning decision rather than something a check does on
    its own.
    """

    @staticmethod
    def _shell() -> Optional[str]:
        for candidate in ("mongosh", "mongo"):
            if shutil.which(candidate):
                return candidate
        return None

    @staticmethod
    def _connection(environment: Dict[str, str]):
        missing = [
            name for name in (
                "CEDAR_MONGO_HOST", "CEDAR_MONGO_PORT",
                "CEDAR_MONGO_APP_USER_NAME", "CEDAR_MONGO_APP_USER_PASSWORD")
            if not environment.get(name)
        ]
        if missing:
            return None, missing
        # The app user is created in the application database, so it is also the authentication
        # database and needs no authSource
        uri = "mongodb://{user}:{password}@{host}:{port}/cedar".format(
            user=environment["CEDAR_MONGO_APP_USER_NAME"],
            password=environment["CEDAR_MONGO_APP_USER_PASSWORD"],
            host=environment["CEDAR_MONGO_HOST"],
            port=environment["CEDAR_MONGO_PORT"])
        return uri, []

    @staticmethod
    def _rows(output: List[str]):
        rows = []
        for line in output:
            parts = line.strip().split("\t")
            if len(parts) == 3 and parts[0] in ARTIFACT_COLLECTIONS:
                rows.append((parts[0], parts[1], parts[2]))
        return rows

    @classmethod
    def check_stores(cls, runner=None) -> int:
        """Report each artifact collection's unique @id index; non-zero when one is missing."""
        shell = cls._shell()
        if shell is None:
            console.print(
                "[red]No Mongo shell on this host: install mongosh, or run the check where the "
                "store is reachable.[/red]")
            return 1

        surface, environment = "native", ModeManager.profile_environment("native")
        uri, missing = cls._connection(environment)
        if uri is None:
            console.print(
                f"[red]The {surface} environment carries no Mongo credentials: "
                f"{', '.join(missing)}.[/red]")
            return 1

        script = PROBE_SCRIPT % json.dumps(ARTIFACT_COLLECTIONS)
        command = "{shell} {uri} --quiet --eval {script}".format(
            shell=shell,
            uri=_quote(uri),
            script=_quote(script))
        # Never echoed: the command line carries the store's password
        run = runner or (lambda: run_shell(command, shell=GlobalContext.get_shell()))
        output = run()
        rows = cls._rows(list(output))

        if getattr(output, "returncode", 0) or not rows:
            console.print(
                "[red]The store did not answer. Its index state is unknown, which is not the same "
                "as provisioned.[/red]")
            return 1

        table = Table("Collection", "Documents", "Unique @id index",
                      title=f"CEDAR artifact document store ({environment['CEDAR_MONGO_HOST']})")
        for name, count, state in rows:
            table.add_row(name, count, "yes" if state == "unique" else "[red]NO[/red]")
        console.print(table)

        unprovisioned = [name for name, _count, state in rows if state != "unique"]
        reported = {name for name, _count, _state in rows}
        unanswered = [name for name in ARTIFACT_COLLECTIONS if name not in reported]
        if unprovisioned or unanswered:
            for name in unprovisioned:
                console.print(
                    f"[red]{name} enforces no uniqueness and answers every lookup by identifier "
                    f"with a collection scan.[/red]")
            for name in unanswered:
                console.print(f"[red]{name} was not reported by the store.[/red]")
            console.print(
                "Provision with the admin tool's artifactServer-initDB task, after counting "
                "repeated identifiers: a unique build over one fails.")
            return 1

        console.print("Every artifact collection carries its unique @id index.")
        return 0


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
