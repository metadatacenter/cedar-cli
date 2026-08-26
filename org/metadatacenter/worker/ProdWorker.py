import os
import re
from pathlib import Path

from rich.console import Console

from org.metadatacenter.util.Const import Const
from org.metadatacenter.util.Util import Util
from org.metadatacenter.worker.Worker import Worker

console = Console()


class ProdError(ValueError):
    pass


class ProdWorker(Worker):

    def __init__(self):
        super().__init__()

    @staticmethod
    def configure_frontends():
        domain = os.environ.get(Const.CEDAR_HOST)
        if not domain:
            raise ProdError("CEDAR_HOST is not set. Load the production CEDAR profile first.")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
            raise ProdError(f"CEDAR_HOST is not a valid hostname suffix: {domain}")

        index_files = ProdWorker.frontend_index_files()
        missing = [str(path) for _, path in index_files if not path.is_file()]
        if missing:
            raise ProdError(f"Cannot configure frontends; missing: {', '.join(missing)}")

        configured = []
        for _, path in index_files:
            content = path.read_text()
            content, replacement_count = re.subn(
                r'window\.cedarDomain\s*=\s*"[^"]*"',
                f'window.cedarDomain = "{domain}"',
                content,
            )
            if replacement_count == 0:
                raise ProdError(f"Cannot find window.cedarDomain in {path}")
            content = content.replace('content.metadatacenter.org/', f'content.{domain}/')
            configured.append((path, content))

        temporary_files = []
        for path, content in configured:
            temp_path = path.with_name(f'.{path.name}.cedarcli.tmp')
            temp_path.write_text(content)
            temp_path.chmod(path.stat().st_mode)
            temporary_files.append((temp_path, path))
        for temp_path, path in temporary_files:
            os.replace(temp_path, path)
        console.print(f"[green]Configured {len(index_files)} frontend entry points for {domain}.[/green]")
        return 0

    @staticmethod
    def reset_frontends():
        for repo_dir, path in ProdWorker.frontend_index_files():
            if not repo_dir.is_dir():
                raise ProdError(f"Cannot reset frontend; repository is missing: {repo_dir}")
            relative_path = path.relative_to(repo_dir)
            result = Worker.execute_generic_shell_commands(
                [f"git restore --source=HEAD -- {relative_path}"],
                cwd=str(repo_dir),
                title=f"Resetting {repo_dir.name} frontend configuration",
            )
            if result.returncode:
                return result.returncode
        return 0

    @staticmethod
    def frontend_index_files():
        cedar_home = Path(Util.cedar_home)
        return [
            (cedar_home / 'cedar-openview',
             cedar_home / 'cedar-openview' / 'cedar-openview-dist' / 'index.html'),
            (cedar_home / 'cedar-bridging',
             cedar_home / 'cedar-bridging' / 'cedar-bridging-dist' / 'index.html'),
            (cedar_home / 'cedar-monitoring',
             cedar_home / 'cedar-monitoring' / 'cedar-monitoring-dist' / 'index.html'),
        ]
